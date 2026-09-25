"""Function tools exposed to the model.

Each tool is a thin, typed wrapper around one platform-bridge tool (or the web
search service) and records a structured event so the user sees *what kind of
step* happened. Platform authority is never decided here: the bridge verifies
the task credential, the live session, the danger guard and the route's own
permission checks for every call.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from agents import FunctionTool, RunContextWrapper

from ...config import AGENT_TASK_MAX_WEB_SEARCHES, AI_ASSISTANT_URL
from .bridge import BridgeAuthorityLost, BridgeClient, BridgeToolError
from .recorder import EventRecorder, clip

MAX_TOOL_OUTPUT_CHARS = 14000
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
ARTIFACT_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".html", ".htm"}
QUESTION_MAX = 3
OPTION_MIN, OPTION_MAX = 2, 4

OBJECT = {"type": "object", "additionalProperties": True}
FILE_SELECTORS = {
    "material_id": {"type": "integer", "minimum": 1, "description": "材料 ID"},
    "submission_file_id": {"type": "integer", "minimum": 1, "description": "作业提交附件 ID"},
    "collaboration_file_id": {"type": "integer", "minimum": 1, "description": "小组协作文件 ID"},
    "course_file_id": {"type": "integer", "minimum": 1, "description": "课程共享文件 ID"},
    "path": {"type": "string", "description": "本任务内的相对路径（如 outputs/报告.md 或 attachments/x.docx）"},
    "parent_task_id": {"type": "integer", "minimum": 1, "description": "读取历史父任务文件时填写"},
}


@dataclass
class RunContext:
    task_id: int
    actor_role: str
    bridge: BridgeClient
    recorder: EventRecorder
    workspace: Path
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    web_searches: int = 0
    pending_question: dict[str, Any] | None = None
    operations: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    authority_lost: str = ""
    call_seq: int = 0

    def next_call_id(self) -> str:
        self.call_seq += 1
        return f"c{self.call_seq}"


class ToolFailure(Exception):
    pass


Handler = Callable[[RunContext, dict[str, Any]], Awaitable[Any]]


def _dump(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "…（结果过长已截断；请缩小查询范围或分页读取）"
    return text


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], handler: Handler) -> FunctionTool:
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    async def invoke(wrapper: RunContextWrapper[RunContext], raw: str) -> str:
        ctx = wrapper.context
        try:
            arguments = json.loads(raw or "{}")
            if not isinstance(arguments, dict):
                raise ToolFailure("参数必须是 JSON 对象。")
            return _dump(await handler(ctx, arguments))
        except BridgeAuthorityLost as exc:
            ctx.authority_lost = str(exc) or "任务授权已失效"
            return _dump({"ok": False, "error": "任务授权已失效（任务被取消、暂停或登录已过期），请立即停止并总结。"})
        except (ToolFailure, BridgeToolError, ValueError, httpx.HTTPError) as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc) or exc.__class__.__name__}
            code = getattr(exc, "code", "")
            if code:
                payload["code"] = code
            return _dump(payload)

    return FunctionTool(name=name, description=description, params_json_schema=schema,
                        on_invoke_tool=invoke, strict_json_schema=False)


# ---------------------------------------------------------------- capability cache

def _remember_capabilities(ctx: RunContext, value: Any) -> None:
    """Cache key -> label/method/path so events can say what an operation is."""
    if isinstance(value, dict):
        key = value.get("key") or value.get("action")
        if isinstance(key, str) and (value.get("path") or value.get("label") or value.get("title")):
            entry = ctx.capabilities.setdefault(key, {})
            for name in ("label", "title", "method", "path", "risk", "mutates", "status", "tool"):
                if name in value:
                    entry[name] = value[name]
        for item in value.values():
            _remember_capabilities(ctx, item)
    elif isinstance(value, list):
        for item in value:
            _remember_capabilities(ctx, item)


def _capability_label(ctx: RunContext, key: str) -> str:
    entry = ctx.capabilities.get(key) or {}
    return str(entry.get("label") or entry.get("title") or key)


def _is_mutating(ctx: RunContext, key: str) -> bool:
    entry = ctx.capabilities.get(key) or {}
    if "mutates" in entry:
        return bool(entry["mutates"])
    method = str(entry.get("method") or "").upper()
    return bool(method) and method != "GET"


# ---------------------------------------------------------------- read-side tools

def _summarize_result(value: Any, limit: int = 140) -> str:
    if isinstance(value, dict):
        for key in ("rows", "items", "results", "tasks", "operations", "platform_routes"):
            items = value.get(key)
            if isinstance(items, list):
                return clip(f"返回 {len(items)} 条结果", limit)
        data = value.get("data")
        if isinstance(data, list):
            return clip(f"返回 {len(data)} 条结果", limit)
        for key in ("content", "text", "digest"):
            if isinstance(value.get(key), str) and value[key]:
                return clip(f"获取到 {len(value[key])} 字内容", limit)
    if isinstance(value, list):
        return clip(f"返回 {len(value)} 条结果", limit)
    return "已返回结果"


async def _read_step(ctx: RunContext, tool: str, label: str, call: Callable[[], Awaitable[Any]], *, target: str = "") -> Any:
    call_id = ctx.next_call_id()
    await ctx.recorder.emit("tool_call", label, {"call_id": call_id, "tool": tool, "label": label, "target": clip(target, 200)})
    try:
        value = await call()
    except BridgeAuthorityLost:
        raise
    except Exception as exc:
        await ctx.recorder.emit("tool_result", f"未成功：{clip(exc, 160)}", {"call_id": call_id, "tool": tool, "ok": False, "summary": str(exc)})
        raise
    summary = _summarize_result(value)
    await ctx.recorder.emit("tool_result", summary, {"call_id": call_id, "tool": tool, "ok": True, "summary": summary})
    return value


async def platform_overview(ctx: RunContext, args: dict[str, Any]) -> Any:
    return await _read_step(ctx, "platform_overview", "读取我的身份与平台概览", lambda: ctx.bridge.call("platform_overview"))


async def find_capabilities(ctx: RunContext, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolFailure("请提供检索关键词，例如“作业 提交”“博客 发布”“课堂 成员”。")
    value = await _read_step(ctx, "find_capabilities", f"检索平台功能：{clip(query, 40)}",
                             lambda: ctx.bridge.call("platform_capabilities", {"query": query[:80]}), target=query)
    _remember_capabilities(ctx, value)
    return value


async def capability_details(ctx: RunContext, args: dict[str, Any]) -> Any:
    keys = [str(item) for item in (args.get("keys") or []) if str(item).strip()][:8]
    if not keys:
        raise ToolFailure("keys 至少包含一个能力名称。")
    value = await _read_step(ctx, "capability_details", f"查看 {len(keys)} 项功能的参数说明",
                             lambda: ctx.bridge.call("platform_capabilities", {"keys": keys}), target=", ".join(keys))
    _remember_capabilities(ctx, value)
    return value


async def platform_read(ctx: RunContext, args: dict[str, Any]) -> Any:
    key = str(args.get("operation_key") or "")
    payload: dict[str, Any] = {"operation_key": key}
    for name in ("path_params", "query_params"):
        if isinstance(args.get(name), dict):
            payload[name] = args[name]
    return await _read_step(ctx, "platform_read", f"读取：{_capability_label(ctx, key)}",
                            lambda: ctx.bridge.call("platform_read", payload), target=key)


async def query_catalog(ctx: RunContext, args: dict[str, Any]) -> Any:
    return await _read_step(ctx, "platform_query_catalog", "查看可用的统计查询", lambda: ctx.bridge.call("platform_query_catalog"))


async def run_query(ctx: RunContext, args: dict[str, Any]) -> Any:
    name = str(args.get("query") or "")
    payload = {"query": name, "params": args.get("params") if isinstance(args.get("params"), dict) else {},
               "limit": max(1, min(int(args.get("limit") or 100), 200))}
    return await _read_step(ctx, "platform_query", f"统计查询：{name}", lambda: ctx.bridge.call("platform_query", payload), target=name)


async def read_file(ctx: RunContext, args: dict[str, Any]) -> Any:
    payload = {key: args[key] for key in FILE_SELECTORS if args.get(key) not in (None, "")}
    label = next((f"{key}={payload[key]}" for key in ("material_id", "submission_file_id", "collaboration_file_id", "course_file_id", "path")
                  if key in payload), "文件")
    return await _read_step(ctx, "platform_file", f"读取文件：{label}", lambda: ctx.bridge.call("platform_file", payload), target=label)


async def request_status(ctx: RunContext, args: dict[str, Any]) -> Any:
    operation_id = str(args.get("operation_id") or "")
    return await _read_step(ctx, "platform_request_status", "核对一次平台操作的回执",
                            lambda: ctx.bridge.call("platform_request_status", {"operation_id": operation_id}), target=operation_id)


async def task_context(ctx: RunContext, args: dict[str, Any]) -> Any:
    payload = {key: int(args[key]) for key in ("task_id", "offset", "limit") if args.get(key) is not None}
    return await _read_step(ctx, "platform_task_context", "读取上一次任务的结果与回执",
                            lambda: ctx.bridge.call("platform_task_context", payload))


# ---------------------------------------------------------------- web

async def web_search(ctx: RunContext, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolFailure("请提供搜索内容。")
    if ctx.web_searches >= AGENT_TASK_MAX_WEB_SEARCHES:
        raise ToolFailure(f"本任务联网搜索次数已达上限（{AGENT_TASK_MAX_WEB_SEARCHES} 次），请基于已有信息完成。")
    ctx.web_searches += 1

    async def search() -> Any:
        async with httpx.AsyncClient(base_url=AI_ASSISTANT_URL, timeout=90.0) as client:
            response = await client.post("/api/ai/web-search", json={
                "query": query[:400], "instructions": str(args.get("focus") or "")[:400] or None,
                "task_label": f"agent_task:web_search:{ctx.task_id}"})
            response.raise_for_status()
            text = str((response.json() or {}).get("text") or "")
        if not text:
            raise ToolFailure("联网搜索暂时没有返回结果，可换个说法或用 web_fetch 读取已知网址。")
        return {"query": query, "text": text}

    return await _read_step(ctx, "web_search", f"联网搜索：{clip(query, 60)}", search, target=query)


async def web_fetch(ctx: RunContext, args: dict[str, Any]) -> Any:
    url = str(args.get("url") or "").strip()
    return await _read_step(ctx, "web_fetch", f"打开网页：{clip(url, 80)}",
                            lambda: ctx.bridge.call("public_fetch", {"url": url, "mode": "text"}), target=url)


# ---------------------------------------------------------------- operations (state changes)

def _operation_summary(value: Any) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(value, dict):
        return True, "已完成", {}
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    status = str(value.get("status") or "")
    http_status = result.get("http_status")
    ok = status in ("observed_http_result", "submitted", "completed", "committed")
    try:
        if http_status is not None and not 200 <= int(http_status) < 300:
            ok = False
    except (TypeError, ValueError):
        pass
    labels = {"observed_http_result": "平台已执行并返回结果", "submitted": "已提交，后台处理中",
              "uncertain": "结果不确定，需要核对", "failed": "执行失败"}
    text = labels.get(status, "已执行" if ok else "未成功")
    if http_status:
        text += f"（HTTP {http_status}）"
    return ok, text, {"status": status, "http_status": http_status}


async def platform_request(ctx: RunContext, args: dict[str, Any]) -> Any:
    key = str(args.get("capability_key") or "").strip()
    if not key:
        raise ToolFailure("capability_key 不能为空。")
    if key not in ctx.capabilities:
        try:
            _remember_capabilities(ctx, await ctx.bridge.call("platform_capabilities", {"keys": [key]}))
        except BridgeToolError:
            pass
    operation_id = str(uuid.uuid4())
    payload: dict[str, Any] = {"capability_key": key, "operation_id": operation_id}
    for name in ("path_params", "query_params", "body"):
        if isinstance(args.get(name), dict):
            payload[name] = args[name]
    if isinstance(args.get("files"), list):
        payload["files"] = args["files"]
    if isinstance(args.get("safety_check"), dict):
        payload["safety_check"] = args["safety_check"]
    entry = ctx.capabilities.get(key) or {}
    label = _capability_label(ctx, key)
    mutating = _is_mutating(ctx, key)
    call_id = ctx.next_call_id()
    base = {"call_id": call_id, "capability_key": key, "label": label, "method": entry.get("method"), "path": entry.get("path"),
            "intent": clip(args.get("intent") or "", 300)}
    if mutating:
        await ctx.recorder.emit("operation", f"执行操作：{clip(args.get('intent') or label, 120)}",
                                {**base, "safety_check": payload.get("safety_check")})
    else:
        await ctx.recorder.emit("tool_call", f"调用接口：{label}", {**base, "tool": "platform_request", "target": key})
    try:
        value = await ctx.bridge.call("platform_request", payload)
    except BridgeToolError as exc:
        if exc.code.startswith("agent_"):
            await ctx.recorder.emit("guard", clip(str(exc), 300), {"call_id": call_id, "code": exc.code, "message": str(exc), "label": label})
        await ctx.recorder.emit("operation_result" if mutating else "tool_result", f"未成功：{clip(exc, 160)}",
                                {"call_id": call_id, "ok": False, "summary": str(exc), "code": exc.code})
        raise
    ok, text, extra = _operation_summary(value)
    await ctx.recorder.emit("operation_result" if mutating else "tool_result", text,
                            {"call_id": call_id, "ok": ok, "summary": text, **extra})
    if mutating:
        ctx.operations.append({"label": clip(args.get("intent") or label, 160), "capability_key": key,
                               "operation_id": operation_id, "ok": ok, **extra})
    if isinstance(value, dict):
        value = {**value, "operation_id": operation_id}
    return value


async def platform_write(ctx: RunContext, args: dict[str, Any]) -> Any:
    action = str(args.get("action") or "").strip()
    if not action:
        raise ToolFailure("action 不能为空。")
    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    operation_id = f"agent-{uuid.uuid4()}"
    label = clip(args.get("intent") or _capability_label(ctx, action), 120)
    call_id = ctx.next_call_id()
    await ctx.recorder.emit("operation", f"执行操作：{label}", {"call_id": call_id, "action": action, "label": label})
    try:
        value = await ctx.bridge.call("platform_write", {"operation_id": operation_id, "action": action, "params": params})
    except BridgeToolError as exc:
        await ctx.recorder.emit("operation_result", f"未成功：{clip(exc, 160)}", {"call_id": call_id, "ok": False, "summary": str(exc)})
        raise
    await ctx.recorder.emit("operation_result", "已完成并取得业务回执", {"call_id": call_id, "ok": True, "summary": "业务回执已记录"})
    ctx.operations.append({"label": label, "action": action, "operation_id": operation_id, "ok": True})
    return value


# ---------------------------------------------------------------- task files

def _safe_artifact_name(name: str) -> str:
    base = Path(str(name or "").replace("\\", "/")).name
    base = re.sub(r"[^\w.一-鿿-]", "-", base).strip("-.")[:80]
    if not base:
        raise ToolFailure("文件名无效。")
    if Path(base).suffix.lower() not in ARTIFACT_EXTENSIONS:
        base += ".md"
    return base


async def save_artifact(ctx: RunContext, args: dict[str, Any]) -> Any:
    name = _safe_artifact_name(str(args.get("filename") or ""))
    data = str(args.get("content") or "").encode("utf-8")
    if not data.strip():
        raise ToolFailure("文件内容为空。")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ToolFailure("文件超过 2MB，请拆分。")
    outputs = ctx.workspace / "outputs"
    if outputs.is_symlink():
        raise ToolFailure("目标路径无效。")
    outputs.mkdir(parents=True, exist_ok=True)
    target = outputs / name
    if target.is_symlink():
        raise ToolFailure("目标路径无效。")
    target.write_bytes(data)
    relative = f"outputs/{name}"
    item = {"path": relative, "name": name, "size": len(data), "title": clip(args.get("title") or name, 80)}
    ctx.artifacts = [entry for entry in ctx.artifacts if entry["path"] != relative] + [item]
    await ctx.recorder.emit("artifact", f"已生成文件：{name}", item)
    return {"ok": True, "path": relative, "size": len(data),
            "note": "文件已保存到本任务，用户可在结果中下载；支持附件的 platform_request 可用 files=[{path}] 上传到平台。"}


# ---------------------------------------------------------------- decisions and questions

async def record_decision(ctx: RunContext, args: dict[str, Any]) -> Any:
    decision = clip(args.get("decision") or "", 300)
    if not decision:
        raise ToolFailure("decision 不能为空。")
    steps = [clip(item, 160) for item in (args.get("next_steps") or []) if str(item).strip()][:6]
    await ctx.recorder.emit("decision", decision, {"decision": decision, "rationale": clip(args.get("rationale") or "", 1200),
                                                    "next_steps": steps})
    return {"ok": True, "recorded": True}


def normalize_questions(args: dict[str, Any]) -> dict[str, Any]:
    raw_questions = args.get("questions")
    if not isinstance(raw_questions, list) or not 1 <= len(raw_questions) <= QUESTION_MAX:
        raise ToolFailure(f"questions 需要 1~{QUESTION_MAX} 个问题。")
    questions = []
    for index, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            raise ToolFailure("每个问题必须是对象。")
        text = clip(item.get("question") or "", 300)
        options = []
        for option in item.get("options") or []:
            if isinstance(option, str):
                option = {"label": option}
            if isinstance(option, dict) and str(option.get("label") or "").strip():
                options.append({"label": clip(option["label"], 80), "description": clip(option.get("description") or "", 200)})
        if not text or not OPTION_MIN <= len(options) <= OPTION_MAX:
            raise ToolFailure(f"每个问题需要清楚的 question 与 {OPTION_MIN}~{OPTION_MAX} 个选项（最推荐的放第一个；自定义输入由平台自动追加）。")
        questions.append({"id": f"q{index}", "question": text, "detail": clip(item.get("detail") or "", 400),
                          "options": options, "multi_select": bool(item.get("multi_select"))})
    return {"title": clip(args.get("title") or "需要你确认", 80), "context": clip(args.get("context") or "", 600),
            "questions": questions}


async def ask_user(ctx: RunContext, args: dict[str, Any]) -> Any:
    ctx.pending_question = normalize_questions(args)
    # The runner parks the task after this turn (StopAtTools) and records the event.
    return {"ok": True, "status": "waiting_for_user", "note": "问题已发送给用户。任务会暂停并释放队列，用户回答后自动继续。"}


# ---------------------------------------------------------------- registry

SAFETY_CHECK_SCHEMA = {
    "type": "object",
    "description": "破坏性操作（删除/撤销/清空/批量/覆盖等）必填的自检。先自问：这是用户明确要求的吗？数据确认无效或过期吗？影响多少条？",
    "properties": {
        "user_requested": {"type": "boolean", "description": "用户是否明确要求了这个修改/删除"},
        "data_state": {"type": "string", "enum": ["invalid", "expired", "user_confirmed", "user_specified"],
                       "description": "invalid=已确认无效；expired=已确认过期；user_confirmed=用户已在本任务的选项中确认；user_specified=用户明确点名了目标"},
        "reason": {"type": "string", "description": "为什么这次修改/删除是合理的（具体到数据）"},
        "target_count": {"type": "integer", "minimum": 1, "description": "影响的数据条数"},
        "targets": {"type": "string", "description": "影响对象摘要"},
        "question_id": {"type": "string", "description": "data_state=user_confirmed 时必填：用户最近一次回答的疑问编号（见回答消息）"},
    },
    "required": ["user_requested", "data_state", "reason", "target_count"],
}

ASK_USER_SCHEMA = {
    "title": {"type": "string", "description": "一句话概括要确认什么"},
    "context": {"type": "string", "description": "必要背景（简短）"},
    "questions": {"type": "array", "minItems": 1, "maxItems": QUESTION_MAX, "items": {
        "type": "object", "properties": {
            "question": {"type": "string"}, "detail": {"type": "string"},
            "options": {"type": "array", "minItems": OPTION_MIN, "maxItems": OPTION_MAX, "items": {
                "type": "object", "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                "required": ["label"]}},
            "multi_select": {"type": "boolean"}},
        "required": ["question", "options"]}},
}


def build_tools(ctx: RunContext) -> list[FunctionTool]:
    tools = [
        _tool("platform_overview", "读取当前用户身份、角色、权限概况和平台功能版图。任务开始时先调用一次。", {}, [], platform_overview),
        _tool("find_capabilities", "按中文或英文关键词检索平台可用功能（读取、操作、全站接口 route.*）。返回能力名称索引。",
              {"query": {"type": "string", "description": "关键词，如“作业 批改”“博客 发布”“课堂 学生名单”"}}, ["query"], find_capabilities),
        _tool("capability_details", "获取所选功能（1~8 个 key）的完整参数、方法与路径。执行前必须先看参数。",
              {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}}, ["keys"], capability_details),
        _tool("platform_read", "通过已审核的只读接口读取本人有权访问的数据（operation_key 来自能力目录）。",
              {"operation_key": {"type": "string"}, "path_params": OBJECT, "query_params": OBJECT}, ["operation_key"], platform_read),
        _tool("platform_request", "以用户本人身份调用平台接口（审核能力或 route.* 全站接口），用于查询或执行操作。"
              "删除/撤销/清空/批量/覆盖类操作必须附 safety_check；硬性拦截的高危操作不会被执行。返回平台回执与 operation_id。",
              {"capability_key": {"type": "string"}, "path_params": OBJECT, "query_params": OBJECT, "body": OBJECT,
               "files": {"type": "array", "items": {"type": "object"}, "description": "支持附件的表单能力：引用本任务相对路径 {path}"},
               "intent": {"type": "string", "description": "一句话说明这次调用要达成什么（展示给用户）"},
               "safety_check": SAFETY_CHECK_SCHEMA},
              ["capability_key"], platform_request),
        _tool("platform_write", "执行已审核的事务型平台操作（action 来自能力目录的 writes），返回业务回执。",
              {"action": {"type": "string"}, "params": OBJECT, "intent": {"type": "string"}}, ["action", "params"], platform_write),
        _tool("request_status", "核对一次已发送的平台操作回执（用 platform_request 返回的 operation_id）；结果不确定时先核对，不要重复执行。",
              {"operation_id": {"type": "string"}}, ["operation_id"], request_status),
        _tool("read_platform_file", "读取本人有权下载的平台文件文本（材料、作业附件、小组文件、课程文件）或本任务文件。五种来源选一种。",
              dict(FILE_SELECTORS), [], read_file),
        _tool("task_context", "追问/续做时读取上一次任务的结果、回执与产物，避免重复执行已完成的操作。",
              {"task_id": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
               "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, [], task_context),
        _tool("web_search", "联网搜索最新、准确的公开信息（政策、资料、新闻、技术文档），返回带来源的摘要。",
              {"query": {"type": "string"}, "focus": {"type": "string", "description": "希望重点关注的方面（可选）"}}, ["query"], web_search),
        _tool("web_fetch", "打开一个公网网页并读取正文（不能访问内网）。", {"url": {"type": "string"}}, ["url"], web_fetch),
        _tool("save_artifact", "把成品（报告、名单、文稿、表格 CSV、网页）保存为本任务文件，供用户下载或后续上传。",
              {"filename": {"type": "string", "description": "如 期中成绩分析.md / 名单.csv"}, "content": {"type": "string"},
               "title": {"type": "string"}}, ["filename", "content"], save_artifact),
        _tool("record_decision", "在关键节点记录你的决定：做什么、为什么、接下来几步。会以“决定”展示给用户。开始执行、改变方案、执行重要操作前调用。",
              {"decision": {"type": "string"}, "rationale": {"type": "string"},
               "next_steps": {"type": "array", "items": {"type": "string"}, "maxItems": 6}}, ["decision"], record_decision),
        _tool("ask_user", "当用户意图不明确、存在多个合理方案或大量修改/删除需要确认时，向用户提出 1~3 个简短问题，"
              "每个问题给 2~4 个选项（最合理的放第一个）。平台会自动追加“自定义输入”。提问后任务暂停并释放队列，用户回答后继续。",
              ASK_USER_SCHEMA, ["title", "questions"], ask_user),
    ]
    if ctx.actor_role == "teacher":
        tools[4:4] = [
            _tool("query_catalog", "列出可用的命名统计查询（作业提交、成绩、出勤、公文等）及参数。", {}, [], query_catalog),
            _tool("run_query", "执行命名统计查询（范围由当前身份自动限定，不支持任意 SQL）。",
                  {"query": {"type": "string"}, "params": OBJECT, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                  ["query"], run_query),
        ]
    return tools
