"""First-party game runtime routes. Game frames never receive first-party credentials."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

from ..services import minigame_platform as platform
from ..services import minigame_runtime as runtime

router = APIRouter(prefix="/api/games/v1", tags=["小游戏游玩"])


class StreamWaiters:
    """One indexed batch of stream watermarks can wake every interested local browser."""

    def __init__(self):
        self.streams = {}
        self.task = None

    async def changed(self, key, after, seconds):
        future = asyncio.get_running_loop().create_future()
        self.streams.setdefault(key, {})[future] = after
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.observe())
        try:
            await asyncio.wait_for(future, seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            group = self.streams.get(key, {})
            group.pop(future, None)
            if not group:
                self.streams.pop(key, None)

    async def observe(self):
        while self.streams:
            # Registration precedes this authoritative water level read. A commit
            # between the authenticated snapshot and registration cannot get lost.
            watched = tuple(self.streams)
            try:
                values = await platform.run_db(runtime.stream_watermarks, watched)
            except Exception as exc:
                for key in watched:
                    for future in self.streams.get(key, {}):
                        if not future.done():
                            future.set_exception(exc)
                return
            for key in watched:
                for future, after in self.streams.get(key, {}).items():
                    if values.get(key) != after and not future.done():
                        future.set_result(None)
            await asyncio.sleep(0.4)


waiters = StreamWaiters()


async def input_body(request):
    if request.method == "GET":
        return dict(request.query_params)
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 65536:
            raise HTTPException(413, "游戏操作超出大小限制")
    if not data:
        return {}
    try:
        body = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "游戏操作必须为 JSON 对象")
    if not isinstance(body, dict):
        raise HTTPException(422, "游戏操作必须为 JSON 对象")
    for key in ("roomId", "runId", "ticketId", "templateId", "gameId", "hostInstance"):
        if body.get(key) is not None and (not isinstance(body[key], str) or not 1 <= len(body[key]) <= 128):
            raise HTTPException(422, "游戏操作标识无效")
    for key in ("expectedRevision", "expectedPrivateRevision", "controlEpoch"):
        value = body.get(key)
        if value is not None and (type(value) is not int or not 0 <= value < 2**53):
            raise HTTPException(422, "游戏操作修订号无效")
    return body


def register(path, method, op, kind="account"):
    async def endpoint(request: Request):
        user = platform.decode_player(request)
        mutation = method != "GET"
        if mutation:
            platform.check_write(request, user)
        body = await input_body(request)
        target = next(iter(request.path_params.values()), "")
        if op == "sync" or op == "pulse":
            try:
                wait = max(0, min(8000, int(body.get("waitMs", 0))))
                after = int(body.get("after", -1))
            except (TypeError, ValueError):
                raise HTTPException(422, "同步参数有误")
            response = await platform.run_db(runtime.poll, user, body, mutation)
            if (
                op == "sync"
                and wait
                and response["throughSeq"] == after
                and response["snapshot"]["phase"] in ("waiting", "countdown", "running", "reconnecting")
            ):
                key = (
                    "activity"
                    if response["snapshot"].get("roomId") or response["snapshot"].get("runId")
                    else "ticket",
                    response["streamId"],
                )
                await waiters.changed(key, after, wait / 1000)
                # Every caller rechecks their own login, game permission and membership,
                # including after a timeout, removal, or publication change.
                response = await platform.run_db(runtime.poll, user, body, False)
        elif mutation:
            key = request.headers.get("Idempotency-Key")
            source = request.client.host if request.client else ""
            response = await platform.run_db(runtime.write, user, op, kind, target, body, key, source)
        else:
            response = await platform.run_db(runtime.read, user, op, target, body)
        # Runtime projections already consist of JSON primitives. Avoid a second
        # deep walk of every roster on the main event loop.
        return JSONResponse(response, headers={"Cache-Control": "private, no-store"})

    endpoint.__name__ = "game_" + op + "_" + method.lower() + "_" + kind
    router.add_api_route(path, endpoint, methods=[method])


for path, method, op, kind in (
    ("/me/activity", "GET", "activity", "account"),
    ("/me/history", "GET", "history", "account"),
    ("/me/teams", "GET", "teams", "account"),
    ("/me/teams/{templateId}", "DELETE", "delete_template", "template"),
    ("/me/preferences", "GET", "prefs", "account"),
    ("/me/preferences", "PUT", "prefs", "account"),
    ("/me/progress/{gameId}", "GET", "progress", "game"),
    ("/me/progress/{gameId}/private", "PUT", "private", "game"),
    ("/games/{gameId}/runs", "POST", "solo", "game"),
    ("/games/{gameId}/rooms", "POST", "create", "game"),
    ("/games/{gameId}/rooms/join", "POST", "join", "game"),
    ("/games/{gameId}/queue", "POST", "queue", "game"),
    ("/games/{gameId}/takeover", "POST", "takeover", "game"),
    ("/rooms/{roomId}/rotate-code", "POST", "rotate", "room"),
    ("/rooms/{roomId}/chat", "POST", "chat", "room"),
    ("/rooms/{roomId}/leave", "POST", "leave", "room"),
    ("/queue/{ticketId}/cancel", "POST", "cancel", "ticket"),
    ("/runs/{runId}/intents", "POST", "intent", "run"),
    ("/runs/{runId}/leave", "POST", "leave", "run"),
    ("/runs/{runId}/finish", "POST", "finish", "run"),
    ("/me/heartbeat", "POST", "pulse", "account"),
    ("/sync", "GET", "sync", "account"),
    ("/sync", "POST", "sync", "account"),
):
    register(path, method, op, kind)
