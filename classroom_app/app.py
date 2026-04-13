import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
import sys
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

# 修复：导入 templates 以便在错误处理程序中使用
from .core import app, ai_client, templates
# 修复：移除 CONFIG_FILE 和 CHAT_LOG_DIR (后者在 V4.0 services/chat_handler.py 中管理)
from .config import AI_ASSISTANT_URL, BASE_DIR, DB_PATH, STATIC_DIR
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
from .services.message_center_service import schedule_pending_private_ai_reply_jobs
from .services.ui_copy_service import (
    ensure_ui_copy_snapshot,
    start_ui_copy_refresh_scheduler,
    stop_ui_copy_refresh_scheduler,
)

# 导入所有 V4.0 路由
from .routers import ui, files, homework, ai, materials, emoji, behavior, message_center
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
    await ai_client.__aenter__()  # 启动 HTTP 客户端
    resumed_private_ai_jobs = schedule_pending_private_ai_reply_jobs()
    if resumed_private_ai_jobs:
        print(f"[MESSAGE_CENTER] 恢复 {resumed_private_ai_jobs} 个待处理的 AI 私信任务")
    try:
        await ensure_ui_copy_snapshot(reason="startup")
    except Exception as exc:
        print(f"[UI_COPY] 启动时刷新界面文案失败: {exc}")
    start_behavior_write_pipeline()
    start_behavior_profile_scheduler()
    start_ui_copy_refresh_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    await stop_ui_copy_refresh_scheduler()
    await stop_behavior_profile_scheduler()
    stop_behavior_write_pipeline()
    await ai_client.__aexit__(None, None, None)  # 关闭 HTTP 客户端
    print("[SERVER] FastAPI 应用已关闭。")


# -----------------
# 新增：全局异常处理器
# -----------------

@app.get("/api/internal/health")
async def internal_health():
    behavior_stats = get_behavior_write_pipeline_stats()
    return {
        "status": "ok",
        "service": "main",
        "ai_assistant_url": AI_ASSISTANT_URL,
        "database_path": str(DB_PATH),
        "behavior_write_worker_alive": behavior_stats["alive"],
        "behavior_write_queue_depth": behavior_stats["queue_depth"],
        "behavior_write_queue_capacity": behavior_stats["queue_capacity"],
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
app.include_router(manage_router.router)

app.include_router(session_router.router)

