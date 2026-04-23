import json
from pathlib import Path
import anyio.to_thread
from fastapi import FastAPI, Request, HTTPException
import sys
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

# 修复：导入 templates 以便在错误处理程序中使用
from .core import app, ai_client, templates
# 修复：移除 CONFIG_FILE 和 CHAT_LOG_DIR (后者在 V4.0 services/chat_handler.py 中管理)
from .config import AI_ASSISTANT_URL, BASE_DIR, DB_PATH, MAIN_THREADPOOL_TOKENS, STATIC_DIR
from .database import init_database
from .dependencies import build_login_redirect_url, build_permission_warning_url
from .dependencies import clear_access_token_cookie, get_active_user_from_request
from .dependencies import infer_required_role_from_path
from .services.behavior_tracking_service import (
    get_behavior_write_pipeline_stats,
    start_behavior_profile_scheduler,
    start_behavior_write_pipeline,
    stop_behavior_profile_scheduler,
    stop_behavior_write_pipeline,
)
from .services.discussion_mood_service import stop_discussion_mood_refresh_tasks
from .services.message_center_service import schedule_pending_private_ai_reply_jobs
from .services.runtime_metrics_service import begin_http_request, finish_http_request, get_runtime_metrics_snapshot
from .services.submission_file_alignment import repair_stale_stored_paths
from .services.assignment_lifecycle_service import close_overdue_assignments
from .database import get_db_connection

# 导入所有 V4.0 路由
from .routers import ui, files, homework, ai, materials, emoji, behavior, message_center, profile
from .routers import manage as manage_router  # 避免命名冲突
from .routers import session as session_router


# -----------------
# 生命周期事件
# -----------------

@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    print("[SERVER] FastAPI 应用启动...")
    # V4.0: 启动时不再读取 config.json。
    # 数据库初始化已移至 main.py 启动器，但在这里再执行一次以确保 worker 进程也能访问
    # （尽管 uvicorn reload 模式下可能不需要，但这是个好习惯）
    init_database()

    # 确保静态目录存在
    STATIC_DIR.mkdir(exist_ok=True)
    thread_limiter = anyio.to_thread.current_default_thread_limiter()
    if thread_limiter.total_tokens < MAIN_THREADPOOL_TOKENS:
        thread_limiter.total_tokens = MAIN_THREADPOOL_TOKENS
    print(f"[SERVER] 默认线程池容量: {thread_limiter.total_tokens}")
    await ai_client.__aenter__()  # 启动 HTTP 客户端
    resumed_private_ai_jobs = schedule_pending_private_ai_reply_jobs()
    if resumed_private_ai_jobs:
        print(f"[MESSAGE_CENTER] 恢复 {resumed_private_ai_jobs} 个待处理的 AI 私信任务")
    start_behavior_write_pipeline()
    start_behavior_profile_scheduler()

    # Auto-repair stale stored_path entries (e.g. wrong drive letter after migration)
    try:
        with get_db_connection() as align_conn:
            repair_report = repair_stale_stored_paths(align_conn)
            closed_count = close_overdue_assignments(align_conn)
            align_conn.commit()
        if repair_report.paths_repaired > 0 or repair_report.paths_still_missing > 0:
            print(
                f"[ALIGNMENT] stored_path repair: "
                f"{repair_report.paths_repaired} repaired, "
                f"{repair_report.paths_already_valid} valid, "
                f"{repair_report.paths_still_missing} still missing"
            )
        if closed_count > 0:
            print(f"[ASSIGNMENT] startup auto-close completed: {closed_count} assignment(s) closed")
    except Exception as exc:
        print(f"[ALIGNMENT] stored_path auto-repair failed (non-fatal): {exc}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    await stop_discussion_mood_refresh_tasks()
    await stop_behavior_profile_scheduler()
    stop_behavior_write_pipeline()
    await ai_client.__aexit__(None, None, None)  # 关闭 HTTP 客户端
    print("[SERVER] FastAPI 应用已关闭。")


# -----------------
# 新增：全局异常处理器
# -----------------


def _resolve_request_route_template(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return str(route_path or request.url.path or "/")


@app.middleware("http")
async def runtime_metrics_middleware(request: Request, call_next):
    started_at = begin_http_request()
    try:
        response = await call_next(request)
    except Exception as exc:
        finish_http_request(
            started_at=started_at,
            method=request.method,
            route_path=_resolve_request_route_template(request),
            fallback_path=request.url.path,
            status_code=500,
            error_message=str(exc),
        )
        raise

    finish_http_request(
        started_at=started_at,
        method=request.method,
        route_path=_resolve_request_route_template(request),
        fallback_path=request.url.path,
        status_code=response.status_code,
    )
    return response

@app.get("/api/internal/health")
async def internal_health():
    behavior_stats = get_behavior_write_pipeline_stats()
    thread_limiter = anyio.to_thread.current_default_thread_limiter()
    return {
        "status": "ok",
        "service": "main",
        "ai_assistant_url": AI_ASSISTANT_URL,
        "database_path": str(DB_PATH),
        "threadpool_tokens": int(thread_limiter.total_tokens),
        "behavior_write_worker_alive": behavior_stats["alive"],
        "behavior_write_queue_depth": behavior_stats["queue_depth"],
        "behavior_write_queue_capacity": behavior_stats["queue_capacity"],
    }


@app.get("/api/internal/metrics")
async def internal_metrics():
    from .services.chat_handler import manager

    room_connection_total = sum(len(room_connections) for room_connections in manager.rooms.values())
    room_participant_total = sum(len(participants) for participants in manager.room_participants.values())

    return {
        "status": "ok",
        "service": "main",
        "database_path": str(DB_PATH),
        "runtime": get_runtime_metrics_snapshot(),
        "discussion_runtime": {
            "room_count": len(manager.rooms),
            "active_socket_count": int(room_connection_total),
            "active_participant_count": int(room_participant_total),
            "rooms": {
                str(room_id): {
                    "socket_count": len(room_connections),
                    "participant_count": len(manager.room_participants.get(room_id, {})),
                }
                for room_id, room_connections in manager.rooms.items()
            },
        },
    }


def _is_api_request(request: Request) -> bool:
    return request.url.path.startswith("/api")


@app.exception_handler(401)
async def unauthorized_exception_handler(request: Request, exc: HTTPException):
    login_url = build_login_redirect_url(request)
    detail = exc.detail if isinstance(exc.detail, str) else "登录状态已失效，请重新登录。"

    if _is_api_request(request):
        response = JSONResponse({
            "detail": detail,
            "code": "login_required",
            "redirect_to": login_url,
        }, status_code=401)
        clear_access_token_cookie(response)
        return response

    response = RedirectResponse(url=login_url, status_code=303)
    clear_access_token_cookie(response)
    return response


@app.exception_handler(403)
async def forbidden_exception_handler(request: Request, exc: HTTPException):
    user_hint = get_active_user_from_request(request)
    required_role = None
    if exc.headers:
        required_role = exc.headers.get("X-Required-Role")
    required_role = required_role or infer_required_role_from_path(request.url.path)

    if not user_hint:
        login_url = build_login_redirect_url(request)
        if _is_api_request(request):
            response = JSONResponse({
                "detail": "登录状态已失效，请重新登录。",
                "code": "login_required",
                "redirect_to": login_url,
            }, status_code=401)
            clear_access_token_cookie(response)
            return response

        response = RedirectResponse(url=login_url, status_code=303)
        clear_access_token_cookie(response)
        return response

    warning_url = build_permission_warning_url(request, required_role=required_role)
    detail = exc.detail if isinstance(exc.detail, str) else "当前账号没有访问该页面或资源的权限。"

    if _is_api_request(request):
        return JSONResponse({
            "detail": detail,
            "code": "permission_denied",
            "required_role": required_role,
            "redirect_to": warning_url,
        }, status_code=403)

    return RedirectResponse(url=warning_url, status_code=303)


@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: HTTPException):
    """
    捕获所有 404 (Not Found) 错误，并返回一个友好的 HTML 页面。
    """
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "接口不存在"}, status_code=404)

    return templates.TemplateResponse(request, "error.html", {
        "request": request,
        "error_code": 404,
        "error_title": "页面未找到",
        "error_message": "抱歉，您要查找的页面不存在。请检查URL或返回仪表盘。",
        "back_url": "/"
    }, status_code=404)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    捕获所有 500 (Internal Server Error) 错误，返回友好页面。
    """
    # 也在控制台打印详细错误，方便调试
    print(f"[ERROR] 发生未捕获的异常: {exc}", file=sys.stderr)
    import traceback
    traceback.print_exc()

    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "服务器内部错误，请稍后重试"}, status_code=500)

    return templates.TemplateResponse(request, "error.html", {
        "request": request,
        "error_code": 500,
        "error_title": "服务器内部错误",
        "error_message": "抱歉，服务器遇到了一些问题，我们正在处理。",
        "back_url": "/"
    }, status_code=500)


# -----------------
# 挂载静态文件
# -----------------
# 允许模板访问 /static/style.css 等
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------
# 组装路由
# -----------------
app.include_router(ui.router)
app.include_router(files.router)
app.include_router(homework.router)
app.include_router(ai.router)
app.include_router(materials.router)
app.include_router(emoji.router)
app.include_router(behavior.router)
app.include_router(message_center.router)
app.include_router(profile.router)
app.include_router(manage_router.router)

app.include_router(session_router.router)

