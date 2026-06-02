import httpx
import asyncio
import pandas as pd
from collections import deque
from typing import Dict, Any, Optional
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from .config import TEMPLATES_DIR, MAX_HISTORY_IN_MEMORY, AI_ASSISTANT_URL, SITE_RECORD
from .frontend_assets import asset_url, vite_entry_tags
from .time_utils import format_local_datetime

# FastAPI 应用实例
app = FastAPI()

# 添加日期格式化过滤器
def datetime_format(value, format="%Y-%m-%d %H:%M"):
    if value is None:
        return "未知"
    try:
        return format_local_datetime(value, format, fallback=str(value))
    except Exception:
        return str(value)

# 在模板环境中注册过滤器
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["datetime_format"] = datetime_format
templates.env.globals["asset_url"] = asset_url
templates.env.globals["vite_entry_tags"] = vite_entry_tags
templates.env.globals["site_record"] = SITE_RECORD

# AI 服务的 HTTP 客户端
ai_client = httpx.AsyncClient(base_url=AI_ASSISTANT_URL, timeout=120.0)

# 修复：COURSE_INFO 是必须的，用于存放由启动器传入的配置
COURSE_INFO: Dict[str, Any] = {
    "class_name": "未设置",
    "course_name": "未设置",
    "roster_path": None,
    "attendance_path": None,
    "chat_log_path": None,
    "students_df": pd.DataFrame(),
}

# 运行时状态
active_downloads: Dict[str, asyncio.Event] = {}

# 聊天记录现在必须按房间(课堂)管理
chat_histories: Dict[int, deque] = {} # Key: class_offering_id, Value: deque

# 锁
attendance_lock = asyncio.Lock()
chat_log_lock = asyncio.Lock()

