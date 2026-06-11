"""Agent 任务过程流与失败自愈的纯逻辑（G1 / G10）。

- diff_runtime_snapshot：对比两次 deepseek-tui 任务快照，产出增量过程事件
  （人话化描述），由 worker 写入 ``agent_task_events``，前端增量轮询展示。
- classify_runtime_error：把运行时错误归类为 transient（可自动重试）/
  content（重试时附失败原因）/ fatal（不自动重试，保留人工重试按钮）。
"""
from __future__ import annotations

import re
from typing import Any

MAX_EVENT_MESSAGE_CHARS = 300
MAX_NEW_EVENTS_PER_DIFF = 12

ERROR_CLASS_TRANSIENT = "transient"
ERROR_CLASS_CONTENT = "content"
ERROR_CLASS_FATAL = "fatal"

# 工具调用名 -> 中文动作描述（未知工具显示原始名，不阻塞）。
TOOL_NAME_LABELS: dict[str, str] = {
    "shell": "正在执行命令",
    "bash": "正在执行命令",
    "exec": "正在执行命令",
    "curl": "正在访问网络接口",
    "http": "正在访问网络接口",
    "web": "正在抓取网页",
    "fetch": "正在抓取网页",
    "browser": "正在抓取网页",
    "search": "正在联网搜索",
    "read": "正在读取文件",
    "read_file": "正在读取文件",
    "cat": "正在读取文件",
    "write": "正在写入任务产物",
    "write_file": "正在写入任务产物",
    "edit": "正在编辑任务产物",
    "ls": "正在浏览任务目录",
    "list": "正在浏览任务目录",
    "grep": "正在检索文件内容",
    "find": "正在检索文件内容",
    "python": "正在运行脚本",
    "sql": "正在查询平台数据库",
    "query": "正在查询平台数据库",
    "think": "正在思考",
    "plan": "正在规划步骤",
}

_BRIDGE_PATH_LABELS = (
    ("/api/agent-bridge/query", "正在查询平台数据库"),
    ("/api/agent-bridge/schema", "正在读取数据库结构"),
    ("/api/agent-bridge/meta", "正在读取平台与教师画像"),
    ("/api/agent-bridge/search", "正在检索平台内容"),
    ("/api/agent-bridge/file", "正在读取平台文件"),
    ("/api/agent-bridge/web", "正在抓取网页"),
)

_RUNTIME_STATUS_LABELS = {
    "queued": "运行时排队中",
    "pending": "运行时排队中",
    "running": "Agent 正在执行",
    "thinking": "Agent 正在思考",
    "waiting_approval": "等待执行确认",
    "completed": "运行时已完成",
    "failed": "运行时执行失败",
    "canceled": "运行时已取消",
}

_TRANSIENT_ERROR_PATTERN = re.compile(
    r"(timeout|timed?\s*out|超时|connect|connection|网络|temporarily|unavailable|"
    r"rate.?limit|限流|too many requests|429|502|503|504|server overloaded|"
    r"reset by peer|eof occurred|ssl|dns|name resolution)",
    re.IGNORECASE,
)

_CONTENT_ERROR_PATTERN = re.compile(
    r"(json|parse|decode|truncat|截断|不完整|invalid output|malformed|"
    r"length.?limit|max.?tokens|输出超长)",
    re.IGNORECASE,
)

_FATAL_ERROR_PATTERN = re.compile(
    r"(unauthorized|forbidden|401|403|invalid api key|api key|insufficient.?(balance|quota)|"
    r"余额|欠费|content.?policy|违规|安全策略|not configured|未配置)",
    re.IGNORECASE,
)


def _clean(value: Any, *, limit: int = MAX_EVENT_MESSAGE_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def humanize_tool_call(tool_call: Any) -> str:
    """工具调用条目 -> 一句教师能看懂的话。"""
    if isinstance(tool_call, str):
        name = tool_call
        detail_text = ""
    elif isinstance(tool_call, dict):
        name = str(
            tool_call.get("name")
            or tool_call.get("tool")
            or tool_call.get("type")
            or tool_call.get("command")
            or ""
        )
        detail_text = _clean(
            tool_call.get("arguments")
            or tool_call.get("args")
            or tool_call.get("input")
            or tool_call.get("command")
            or tool_call.get("detail")
            or "",
            limit=160,
        )
    else:
        return "正在执行工具调用"

    combined = f"{name} {detail_text}"
    for path, label in _BRIDGE_PATH_LABELS:
        if path in combined:
            return label
    lowered = name.strip().lower()
    label = TOOL_NAME_LABELS.get(lowered)
    if not label:
        for key, value in TOOL_NAME_LABELS.items():
            if key in lowered or (detail_text and key in detail_text.lower()[:40]):
                label = value
                break
    if not label:
        return f"正在调用工具：{_clean(name, limit=60) or '未知工具'}"
    return label


def _timeline_entry_message(entry: Any) -> str:
    if isinstance(entry, str):
        return _clean(entry)
    if isinstance(entry, dict):
        for key in ("message", "title", "summary", "text", "description", "step", "status"):
            value = _clean(entry.get(key))
            if value:
                return value
        return _clean(str({k: entry[k] for k in list(entry)[:3]}), limit=120)
    return ""


def diff_runtime_snapshot(
    prev: dict[str, Any] | None,
    curr: dict[str, Any],
) -> list[dict[str, Any]]:
    """对比两次运行时快照，返回新事件列表。

    每个事件：{"event_type": str, "message": str, "detail": dict}。
    纯函数：不读库、不写库，便于单测。
    """
    events: list[dict[str, Any]] = []
    prev = prev or {}

    prev_status = _clean(prev.get("status"), limit=40)
    curr_status = _clean(curr.get("status"), limit=40)
    if curr_status and curr_status != prev_status:
        label = _RUNTIME_STATUS_LABELS.get(curr_status.lower(), f"运行时状态更新为 {curr_status}")
        events.append(
            {
                "event_type": "runtime_status",
                "message": f"{label}。",
                "detail": {"runtime_status": curr_status},
            }
        )

    prev_timeline = prev.get("timeline") if isinstance(prev.get("timeline"), list) else []
    curr_timeline = curr.get("timeline") if isinstance(curr.get("timeline"), list) else []
    if len(curr_timeline) > len(prev_timeline):
        for index, entry in enumerate(
            curr_timeline[len(prev_timeline):], start=len(prev_timeline) + 1
        ):
            message = _timeline_entry_message(entry)
            if not message:
                message = f"正在执行第 {index} 步"
            events.append(
                {
                    "event_type": "runtime_step",
                    "message": message,
                    "detail": {"step_index": index},
                }
            )

    prev_tools = prev.get("tool_calls") if isinstance(prev.get("tool_calls"), list) else []
    curr_tools = curr.get("tool_calls") if isinstance(curr.get("tool_calls"), list) else []
    if len(curr_tools) > len(prev_tools):
        for index, tool_call in enumerate(
            curr_tools[len(prev_tools):], start=len(prev_tools) + 1
        ):
            events.append(
                {
                    "event_type": "runtime_tool_call",
                    "message": f"{humanize_tool_call(tool_call)}（第 {index} 次工具调用）",
                    "detail": {"tool_index": index},
                }
            )

    if len(events) > MAX_NEW_EVENTS_PER_DIFF:
        dropped = len(events) - MAX_NEW_EVENTS_PER_DIFF
        events = events[:MAX_NEW_EVENTS_PER_DIFF]
        events.append(
            {
                "event_type": "runtime_step",
                "message": f"……以及另外 {dropped} 条过程更新。",
                "detail": {"dropped": dropped},
            }
        )
    return events


def classify_runtime_error(error_text: Any) -> str:
    """运行时错误分类。分类不确定时按 fatal 处理（宁可不自动重试）。"""
    text = str(error_text or "").strip()
    if not text:
        return ERROR_CLASS_FATAL
    if _FATAL_ERROR_PATTERN.search(text):
        return ERROR_CLASS_FATAL
    if _TRANSIENT_ERROR_PATTERN.search(text):
        return ERROR_CLASS_TRANSIENT
    if _CONTENT_ERROR_PATTERN.search(text):
        return ERROR_CLASS_CONTENT
    return ERROR_CLASS_FATAL
