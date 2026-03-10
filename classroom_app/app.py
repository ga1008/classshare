import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
import sys
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

# 修复：导入 templates 以便在错误处理程序中使用
from .core import app, ai_client, templates
# 修复：移除 CONFIG_FILE 和 CHAT_LOG_DIR (后者在 V4.0 services/chat_handler.py 中管理)
from .config import BASE_DIR, STATIC_DIR
from .database import init_database

# 导入所有 V4.0 路由
from .routers import ui, files, homework, ai
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


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    await ai_client.__aexit__(None, None, None)  # 关闭 HTTP 客户端
    print("[SERVER] FastAPI 应用已关闭。")


# -----------------
# 新增：全局异常处理器
# -----------------

@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: HTTPException):
    """
    捕获所有 404 (Not Found) 错误，并返回一个友好的 HTML 页面。
    """
    return templates.TemplateResponse("error.html", {
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

    return templates.TemplateResponse("error.html", {
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
app.include_router(manage_router.router)

app.include_router(session_router.router)

