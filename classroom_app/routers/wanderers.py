"""散修管理 API（docs/wanderer-ai-free-agents-2026-10-25.md §6）。

``/api/manage/system/wanderers/*`` 仅限超管教师；``/api/wanderers/{student_id}/card``
供任意已登录用户识别散修；``/wanderers/{slug}`` 是散修公开主页（登录即可看）。路由由 ``ui_parts/manage_pages_admin.py`` 的 router
汇总挂载（与「散修管理」页面同一入口），不单独改 app.py。

行为引擎与天道（Phase B）按需惰性导入：未就绪时返回 503，而不是让整个
路由模块导入失败。

「立即运行 / 立即审核 / 立即造人」不在 HTTP 请求内执行：校验后以后台任务启动并立即
返回 202；同类已有一轮在本进程运行时返回 409。结束后摘要写入运行时设置
``last_manual_tick_summary`` / ``last_manual_review_summary`` /
``last_manual_genesis_summary``，由概览展示。
"""
from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any, Awaitable, Callable, Coroutine, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import templates
from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services import wanderer_admin_service as admin
from ..services.heavenly_dao_rules import HEAVENLY_DAO_RULES
from ..services.resource_access_service import is_super_admin_teacher
from ..services.wanderer_settings_service import (
    SETTING_LAST_MANUAL_GENESIS_SUMMARY,
    SETTING_LAST_MANUAL_REFLECTION_SUMMARY,
    SETTING_LAST_MANUAL_REVIEW_SUMMARY,
    SETTING_LAST_MANUAL_TICK_SUMMARY,
    wanderer_runtime_setting_set,
)
from ..time_utils import local_iso

router = APIRouter()

MANAGE_PREFIX = "/api/manage/system/wanderers"
ENGINE_NOT_READY = "行为引擎尚未就绪"
RUN_KIND_TICK = "tick"
RUN_KIND_REVIEW = "review"
RUN_KIND_GENESIS = "genesis"
RUN_KIND_REFLECTION = "reflection"
_SUMMARY_SETTING = {
    RUN_KIND_TICK: SETTING_LAST_MANUAL_TICK_SUMMARY,
    RUN_KIND_REVIEW: SETTING_LAST_MANUAL_REVIEW_SUMMARY,
    RUN_KIND_GENESIS: SETTING_LAST_MANUAL_GENESIS_SUMMARY,
    RUN_KIND_REFLECTION: SETTING_LAST_MANUAL_REFLECTION_SUMMARY,
}
# In-process "one manual run per kind" flags; the event loop is single-threaded.
_RUNNING: dict[str, bool] = {RUN_KIND_TICK: False, RUN_KIND_REVIEW: False, RUN_KIND_GENESIS: False,
                             RUN_KIND_REFLECTION: False}
_BACKGROUND_TASKS: set[Any] = set()


def _default_task_runner(coroutine: Coroutine[Any, Any, Any]) -> Any:
    task = asyncio.create_task(coroutine)
    _BACKGROUND_TASKS.add(task)  # keep a strong reference until it finishes
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


# Injectable for tests: receives the coroutine and must eventually run it.
TASK_RUNNER: Callable[[Coroutine[Any, Any, Any]], Any] = _default_task_runner


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _require_super_admin(conn: Any, user: dict) -> None:
    if not is_super_admin_teacher(conn, user.get("id")):
        raise HTTPException(status_code=403, detail="只有超管教师可以管理散修。")


def _run_admin(user: dict, operation: Callable[[Any], Any], *, commit: bool = False) -> Any:
    """Open one connection, check super admin, run, map service errors to HTTP."""
    with get_db_connection() as conn:
        _require_super_admin(conn, user)
        try:
            result = operation(conn)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc) or "散修不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc) or "无权执行该操作") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if commit:
            conn.commit()
        return result


def _ok(**payload: Any) -> dict[str, Any]:
    return {"status": "success", **payload}


# ---------------------------------------------------------------------------
# Phase B engine bridge
# ---------------------------------------------------------------------------

def _engine_function(module_name: str, attribute: str) -> Callable[..., Awaitable[dict]]:
    try:
        module = importlib.import_module(f"classroom_app.services.{module_name}")
        function = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail=ENGINE_NOT_READY) from exc
    return function


def _record_manual_summary(kind: str, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)[:20_000]
    with get_db_connection() as conn:
        wanderer_runtime_setting_set(conn, _SUMMARY_SETTING[kind], text)
        conn.commit()


async def _background_run(kind: str, function: Callable[..., Awaitable[dict]], kwargs: dict[str, Any]) -> None:
    started_at = local_iso()
    try:
        summary = await function(**kwargs)
        payload = {"status": "ok", "summary": summary if isinstance(summary, dict) else {"result": summary}}
    except Exception as exc:  # noqa: BLE001 - background run: report into the summary, never crash the loop
        print(f"[WANDERER] manual {kind} run failed: {type(exc).__name__}: {exc}")
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    try:
        _record_manual_summary(kind, {**payload, "kind": kind, "started_at": started_at, "finished_at": local_iso()})
    except Exception as exc:  # noqa: BLE001 - summary is informational
        print(f"[WANDERER] could not record manual {kind} summary: {exc}")
    finally:
        _RUNNING[kind] = False


def _start_background(kind: str, function: Callable[..., Awaitable[dict]], **kwargs: Any) -> JSONResponse:
    if _RUNNING.get(kind):
        return JSONResponse(status_code=409, content={"status": "error", "message": "已有一轮正在运行"})
    _RUNNING[kind] = True
    try:
        TASK_RUNNER(_background_run(kind, function, kwargs))
    except Exception:
        _RUNNING[kind] = False
        raise
    return JSONResponse(status_code=202, content={"status": "accepted", "message": "已开始，稍后刷新查看结果"})


def _optional_wanderer_id(payload: dict[str, Any]) -> Optional[int]:
    raw = payload.get("wanderer_id")
    if raw in (None, ""):
        return None
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="wanderer_id 必须是整数") from exc
    if number <= 0:
        raise HTTPException(status_code=400, detail="wanderer_id 必须是正整数")
    return number


def _check_target(user: dict, wanderer_id: Optional[int]) -> None:
    """Super-admin check, plus existence of the targeted wanderer when given."""
    if wanderer_id is None:
        _run_admin(user, lambda conn: None)
    else:
        _run_admin(user, lambda conn: admin.require_wanderer(conn, wanderer_id))


# ---------------------------------------------------------------------------
# list / settings / batch actions (static paths before /{wanderer_id})
# ---------------------------------------------------------------------------

@router.get(MANAGE_PREFIX, response_class=JSONResponse)
async def api_list_wanderers(user: dict = Depends(get_current_teacher)):
    def operation(conn):
        return {"overview": admin.build_wanderer_overview(conn), "wanderers": admin.list_wanderer_cards(conn)}

    return _ok(**_run_admin(user, operation), rules=HEAVENLY_DAO_RULES)


@router.get(f"{MANAGE_PREFIX}/settings", response_class=JSONResponse)
async def api_get_wanderer_settings(user: dict = Depends(get_current_teacher)):
    return _ok(settings=_run_admin(user, admin.get_settings))


@router.post(f"{MANAGE_PREFIX}/settings", response_class=JSONResponse)
async def api_update_wanderer_settings(request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    settings = _run_admin(user, lambda conn: admin.update_settings(conn, payload), commit=True)
    return _ok(settings=settings)


@router.post(f"{MANAGE_PREFIX}/reseed", response_class=JSONResponse)
async def api_reseed_wanderers(user: dict = Depends(get_current_teacher)):
    return _ok(report=_run_admin(user, admin.reseed, commit=True))


@router.post(f"{MANAGE_PREFIX}/tick-now", response_class=JSONResponse)
async def api_wanderer_tick_now(request: Request, user: dict = Depends(get_current_teacher)):
    wanderer_id = _optional_wanderer_id(await _json_body(request))
    _check_target(user, wanderer_id)
    function = _engine_function("wanderer_behavior_service", "run_wanderer_tick_once")
    ids = [wanderer_id] if wanderer_id is not None else None
    return _start_background(RUN_KIND_TICK, function, wanderer_ids=ids, force=True)


@router.post(f"{MANAGE_PREFIX}/review-now", response_class=JSONResponse)
async def api_wanderer_dao_review_all(user: dict = Depends(get_current_teacher)):
    _check_target(user, None)
    function = _engine_function("heavenly_dao_service", "run_dao_review_once")
    return _start_background(RUN_KIND_REVIEW, function, force=True)


@router.post(f"{MANAGE_PREFIX}/genesis-now", response_class=JSONResponse)
async def api_wanderer_genesis_now(user: dict = Depends(get_current_teacher)):
    """天道造人, now: ignores the genesis switch and the activity threshold, never the caps."""
    _check_target(user, None)
    function = _engine_function("heavenly_dao_genesis", "run_genesis")
    return _start_background(RUN_KIND_GENESIS, function, manual=True)


# ---------------------------------------------------------------------------
# single wanderer
# ---------------------------------------------------------------------------

@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}", response_class=JSONResponse)
async def api_get_wanderer(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    return _ok(detail=_run_admin(user, lambda conn: admin.build_wanderer_detail(conn, wanderer_id)))


@router.patch(f"{MANAGE_PREFIX}/{{wanderer_id:int}}", response_class=JSONResponse)
async def api_patch_wanderer(wanderer_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    detail = _run_admin(user, lambda conn: admin.update_wanderer(conn, wanderer_id, payload, actor=user), commit=True)
    return _ok(detail=detail)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/status", response_class=JSONResponse)
async def api_set_wanderer_status(wanderer_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    card = _run_admin(user, lambda conn: admin.set_status(conn, wanderer_id, payload.get("status"), actor=user), commit=True)
    return _ok(card=card)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/tier", response_class=JSONResponse)
async def api_set_wanderer_tier(wanderer_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    result = _run_admin(
        user, lambda conn: admin.set_tier(conn, wanderer_id, payload.get("tier"), payload.get("reason"), actor=user),
        commit=True,
    )
    return _ok(**result)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/life", response_class=JSONResponse)
async def api_adjust_wanderer_life(wanderer_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    result = _run_admin(
        user,
        lambda conn: admin.adjust_life(conn, wanderer_id, payload.get("weeks"), payload.get("reason"), actor=user),
        commit=True,
    )
    return _ok(**result)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/persona-upgrade", response_class=JSONResponse)
async def api_upgrade_wanderer_persona(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    result = _run_admin(user, lambda conn: admin.upgrade_persona(conn, wanderer_id), commit=True)
    return _ok(**result)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/reset-warnings", response_class=JSONResponse)
async def api_reset_wanderer_warnings(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    card = _run_admin(user, lambda conn: admin.reset_warnings(conn, wanderer_id, actor=user), commit=True)
    return _ok(card=card)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/wipe", response_class=JSONResponse)
async def api_wipe_wanderer(wanderer_id: int, request: Request, user: dict = Depends(get_current_teacher)):
    payload = await _json_body(request)
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="抹除需要二次确认")
    result = _run_admin(user, lambda conn: admin.wipe(conn, wanderer_id, actor=user), commit=True)
    return _ok(**result)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/revive", response_class=JSONResponse)
async def api_revive_wanderer(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    result = _run_admin(user, lambda conn: admin.revive(conn, wanderer_id, actor=user), commit=True)
    return _ok(**result)


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/review-now", response_class=JSONResponse)
async def api_wanderer_review_now(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    _check_target(user, wanderer_id)
    function = _engine_function("heavenly_dao_service", "run_dao_review_once")
    return _start_background(RUN_KIND_REVIEW, function, wanderer_ids=[wanderer_id], force=True)


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/actions", response_class=JSONResponse)
async def api_list_wanderer_actions(wanderer_id: int, page: int = 1, limit: int = 30,
                                    user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.list_actions(conn, wanderer_id, page=page, limit=limit)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/reviews", response_class=JSONResponse)
async def api_list_wanderer_reviews(wanderer_id: int, page: int = 1, limit: int = 30,
                                    user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.list_reviews(conn, wanderer_id, page=page, limit=limit)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/knowledge", response_class=JSONResponse)
async def api_list_wanderer_knowledge(wanderer_id: int, page: int = 1, limit: int = 50,
                                      user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.list_knowledge(conn, wanderer_id, page=page, limit=limit)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/memory", response_class=JSONResponse)
async def api_list_wanderer_memory(wanderer_id: int, page: int = 1, limit: int = 20, include_archived: bool = False,
                                   user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.memory_page(
        conn, wanderer_id, page=page, limit=limit, include_archived=include_archived)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/reflections", response_class=JSONResponse)
async def api_list_wanderer_reflections(wanderer_id: int, page: int = 1, limit: int = 20,
                                        user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.reflection_page(conn, wanderer_id, page=page, limit=limit)))


@router.post(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/reflect-now", response_class=JSONResponse)
async def api_wanderer_reflect_now(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    _check_target(user, wanderer_id)
    function = _engine_function("wanderer_reflection_service", "run_reflection_now")
    return _start_background(RUN_KIND_REFLECTION, function, wanderer_id=wanderer_id, force=True)


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/social", response_class=JSONResponse)
async def api_wanderer_social(wanderer_id: int, user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.social_admin_view(conn, wanderer_id)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/conversations", response_class=JSONResponse)
async def api_wanderer_conversations(wanderer_id: int, page: int = 1, limit: int = 20,
                                     user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.social_conversations(
        conn, wanderer_id, page=page, limit=limit)))


@router.get(f"{MANAGE_PREFIX}/{{wanderer_id:int}}/conversations/{{conversation_key}}/messages",
            response_class=JSONResponse)
async def api_wanderer_conversation_messages(wanderer_id: int, conversation_key: str, page: int = 1, limit: int = 50,
                                             user: dict = Depends(get_current_teacher)):
    return _ok(**_run_admin(user, lambda conn: admin.social_messages(
        conn, wanderer_id, conversation_key, page=page, limit=limit)))


# ---------------------------------------------------------------------------
# public card
# ---------------------------------------------------------------------------

@router.get("/api/wanderers/{student_id:int}/card", response_class=JSONResponse)
async def api_wanderer_public_card(student_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        card = admin.public_card(conn, student_id)
    if card is None:
        raise HTTPException(status_code=404, detail="不是散修")
    return _ok(card=card)


# ---------------------------------------------------------------------------
# public homepage (design §10.3)
# ---------------------------------------------------------------------------

@router.get("/wanderers/{slug}", response_class=HTMLResponse)
async def wanderer_home_page(request: Request, slug: str, user: dict = Depends(get_current_user)):
    """A 散修's public homepage; any logged-in user. 404 for anything that is not a wanderer slug."""
    from ..services.wanderer_public_service import build_wanderer_home

    with get_db_connection() as conn:
        home = build_wanderer_home(conn, user, slug)
    if home is None:
        raise HTTPException(status_code=404, detail="没有这位散修")
    return templates.TemplateResponse(
        request,
        "wanderers/home.html",
        {"request": request, "user_info": user, "wanderer_home": home},
    )
