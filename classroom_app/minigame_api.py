"""Trusted, same-origin game API for deployments that reserve separate classroom workers."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from . import dependencies
from .routers import minigame_admin, minigame_runtime, minigames
from .services import minigame_platform as platform


@asynccontextmanager
async def lifespan(application):
    # This process authenticates real classroom cookies even for a loopback proxy listener.
    signing_key = dependencies.SECRET_KEY
    if (
        not signing_key
        or signing_key.strip() == "DEFAULT_WEAK_SECRET_KEY_REPLACE_ME"
        or len(signing_key.strip().encode("utf-8")) < 32
    ):
        raise RuntimeError("小游戏专用 API 需要与主站相同的强会话签名密钥（至少 32 字节）")
    try:
        platform.content_origins()
    except HTTPException as exc:
        raise RuntimeError("小游戏专用 API 需要有效的主站与独立内容站来源") from exc
    try:
        await platform.start_minigames(application, dedicated_api=True)
        if not application.state.minigames_ready:
            raise RuntimeError("小游戏专用 API 未就绪，请检查显式数据库迁移与工作进程")
        yield
    finally:
        await platform.stop_minigames(application)


class PrivateResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_private(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(minigames.NO_STORE)
            await send(message)

        await self.app(scope, receive, send_private)


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)
app.state.minigames_ready = False
app.add_middleware(PrivateResponses)
app.include_router(minigames.router)
app.include_router(minigame_admin.router)
app.include_router(minigame_runtime.router)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # Starlette's outer exception handler also needs private headers.
    return JSONResponse({"detail": "小游戏服务暂时不可用"}, status_code=500, headers=minigames.NO_STORE)
