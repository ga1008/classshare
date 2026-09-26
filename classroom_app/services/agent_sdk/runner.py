"""Run one execution segment of an Agent task with the OpenAI Agents SDK.

Segment outcomes:
- final answer             -> finalize ``completed`` (or continue once more if
                              the user sent supplements meanwhile)
- ask_user                 -> park ``waiting_input`` (slot released)
- pause requested/timeout  -> park ``paused`` after the current turn
- cancel / authority lost  -> finalize ``canceled`` / ``failed``
- model or runtime error   -> finalize ``failed`` with partial results
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from agents import Agent, ItemHelpers, Runner
from agents.exceptions import MaxTurnsExceeded
from fastapi import HTTPException

from ...config import (
    AGENT_BRIDGE_BASE_URL,
    AGENT_RUNTIME_ENABLED,
    AGENT_TASK_AUTO_CONTINUE_LIMIT,
    AGENT_TASK_MAX_RUNTIME_SECONDS,
    AGENT_TASK_MAX_TURNS,
)
from ...database import get_db_connection
from ..agent_task_service import (
    AGENT_RUNTIME_PROVIDER,
    RUNTIME_PAUSED,
    RUNTIME_WAITING_INPUT,
    _load_json,
    collect_task_workspace_artifacts,
    task_workspace_paths,
)
from . import state
from .bridge import BridgeClient
from .model import AgentModelUnavailable, build_model, model_settings, resolve_model_target, run_config
from .prompts import answer_message, build_initial_input, build_instructions, supplement_message
from .recorder import EventRecorder, clip
from .tools import RunContext, build_tools

MONITOR_INTERVAL_SECONDS = 2.0
LEASE_RENEW_SECONDS = 15.0
MAX_SUPPLEMENT_ROUNDS = 3
RESUME_NUDGE = {"role": "user", "content": "请从暂停处继续完成任务。"}
STOP_MESSAGES = {
    "canceled": "任务已取消。",
    "authority": "任务授权已失效（登录已过期或权限变更），已停止。",
    "lease_lost": "执行租约丢失，已停止。",
}
AUTO_PAUSE_NOTES = {
    "timeout": "单次执行时间较长，已自动保存进度并暂停；点击“继续”即可接着执行。",
    "max_turns": "本次执行步骤较多，已自动保存进度并暂停；点击“继续”即可接着执行。",
}
# Injected when a segment runs out of turns and the runner continues by itself.
# Exhausting the budget almost always means the model is looping on discovery
# (production task 19 spent ~45 turns on find_capabilities): make it commit.
BUDGET_NUDGE = {"role": "user", "content": (
    "# 系统提示：本段步数已用尽，已自动续跑\n"
    "请停止继续检索或反复尝试。根据已掌握的信息，二选一：\n"
    "1. 直接执行最合理的方案（接口参数按 usage / body_fields / body_hints 填写；返回 4xx 时按 error_detail 修正一次）；\n"
    "2. 若仍缺少关键信息或存在多个方案，用 ask_user 提出具体选项让用户决定。\n"
    "不要再做无结果的关键词检索；无法完成时给出结论并说明需要用户在页面手工处理的事项。")}


def _continue_note(index: int, limit: int) -> str:
    return f"步骤已达单段上限，自动续跑（第 {index}/{limit} 次）：停止探索，直接执行或向用户提问。"


class _SegmentStop:
    def __init__(self) -> None:
        self.reason = ""  # canceled | authority | lease_lost | pause | timeout | max_turns

    def set(self, reason: str) -> bool:
        if self.reason:
            return False
        self.reason = reason
        return True


def _reasoning_text(item: Any) -> str:
    raw = getattr(item, "raw_item", None)
    parts: list[str] = []
    for attribute in ("summary", "content"):
        for part in getattr(raw, attribute, None) or []:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _summary_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = re.sub(r"^[#>*\-\s]+|\*\*", "", line).strip()
        if cleaned:
            return clip(cleaned, 180)
    return ""


def _usage(result: Any) -> dict[str, int]:
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    return {"requests": int(getattr(usage, "requests", 0) or 0), "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0)}


def _model_error_message(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "模型密钥无效或已过期，请超级管理员检查 Agent API Key。"
    if status == 402:
        return "模型账户余额不足，请超级管理员充值后重试。"
    if status == 429:
        return "模型服务繁忙（限流），请稍后重试。"
    if isinstance(status, int) and status >= 500:
        return "模型服务暂时不可用，请稍后重试。"
    if "Timeout" in exc.__class__.__name__:
        return "模型响应超时，请稍后重试或拆分任务。"
    return f"Agent 执行出错：{clip(exc, 300)}"


async def _monitor(result: Any, attempt: dict[str, Any], ctx: RunContext, stop: _SegmentStop, started: float) -> None:
    next_renewal = time.monotonic() + LEASE_RENEW_SECONDS
    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        try:
            signals = await asyncio.to_thread(state.run_signals, attempt)
        except Exception:
            continue
        if signals["canceled"]:
            stop.set("canceled")
            result.cancel("immediate")
            return
        if ctx.authority_lost:
            stop.set("authority")
            result.cancel("immediate")
            return
        if signals["pause"] and stop.set("pause"):
            result.cancel("after_turn")
        if time.monotonic() - started > AGENT_TASK_MAX_RUNTIME_SECONDS and stop.set("timeout"):
            result.cancel("after_turn")
        if time.monotonic() >= next_renewal:
            try:
                await asyncio.to_thread(state.renew_lease, attempt)
            except Exception:
                stop.set("lease_lost")
                result.cancel("immediate")
                return
            next_renewal = time.monotonic() + LEASE_RENEW_SECONDS


async def _stream(result: Any, recorder: EventRecorder) -> str:
    """Forward reasoning and interim messages as events; return the last message text."""
    pending = ""
    async for event in result.stream_events():
        if event.type != "run_item_stream_event":
            continue
        if event.name == "reasoning_item_created":
            text = _reasoning_text(event.item)
            if text:
                await recorder.emit("thinking", clip(text.replace("\n", " "), 120), {"text": text})
        elif event.name == "message_output_created":
            if pending:
                await recorder.emit("assistant_text", clip(pending.replace("\n", " "), 160), {"text": pending})
            pending = ItemHelpers.text_message_output(event.item).strip()
        elif event.name == "tool_called" and pending:
            # Text followed by tool use is an interim explanation, not the answer.
            await recorder.emit("assistant_text", clip(pending.replace("\n", " "), 160), {"text": pending})
            pending = ""
    return pending


def _result_detail(task_id: int, target: Any, ctx: RunContext, usage: dict[str, int], deliverable: str, **extra: Any) -> dict[str, Any]:
    return {"provider": AGENT_RUNTIME_PROVIDER, "model": target.model, "deliverable_markdown": deliverable,
            "artifacts": collect_task_workspace_artifacts(task_id), "operations": ctx.operations,
            "usage": usage, "web_searches": ctx.web_searches, **extra}


def _segment_input(task: dict[str, Any], history: list[Any], question: Any, answers: Any, supplements: list[str]) -> list[Any]:
    if not history:
        return build_initial_input(task)  # already includes pending supplements
    items: list[Any] = list(history)
    if question and answers:
        items.append(answer_message(question, answers))
    if supplements:
        items.append(supplement_message(supplements))
    if not (question and answers) and not supplements:
        items.append(RESUME_NUDGE)
    return items


async def run_agent_task(task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    if not AGENT_RUNTIME_ENABLED:
        await asyncio.to_thread(state.fail_unstarted, task, "Agent 运行时未启用，请联系超级管理员。")
        return
    try:
        task, _actor, attempt, token = await asyncio.to_thread(state.setup_attempt, task_id)
    except HTTPException as exc:
        await asyncio.to_thread(state.fail_unstarted, task, f"无法以你的身份启动 Agent：{clip(exc.detail, 200)}（请重新登录后重试）")
        return
    try:
        def prepare() -> tuple[Any, str]:
            with get_db_connection() as conn:
                return resolve_model_target(conn), build_instructions(task, conn)

        target, instructions = await asyncio.to_thread(prepare)
    except AgentModelUnavailable as exc:
        await asyncio.to_thread(state.finalize_task, attempt, status="failed", summary="",
                                detail={"provider": AGENT_RUNTIME_PROVIDER}, error=str(exc))
        return

    history, question, answers = await asyncio.to_thread(state.consume_resume_state, task_id)
    supplements = await asyncio.to_thread(state.take_pending_supplements, task_id)
    input_items = _segment_input(task, history, question, answers, supplements)

    options = (_load_json(task.get("context_snapshot_json"), {}) or {}).get("agent_options") or {}
    workspace, _runtime = task_workspace_paths(task)
    workspace.mkdir(parents=True, exist_ok=True)
    recorder = EventRecorder(task_id)
    bridge = BridgeClient(AGENT_BRIDGE_BASE_URL, token)
    ctx = RunContext(task_id=task_id, actor_role=str(task.get("actor_role") or "teacher"), bridge=bridge,
                     recorder=recorder, workspace=workspace)
    model, client = build_model(target)
    agent = Agent[RunContext](
        name="LanShare Agent", instructions=instructions, model=model,
        model_settings=model_settings(deep_thinking=bool(options.get("deep_thinking"))),
        tools=build_tools(ctx), tool_use_behavior={"stop_at_tool_names": ["ask_user"]},
    )
    started = time.monotonic()
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    last_text = ""
    try:
        signals = await asyncio.to_thread(state.run_signals, attempt)
        if signals["pause"] and not signals["canceled"]:
            await _park_items(attempt, input_items, RUNTIME_PAUSED, None, usage)
            return
        round_index = 0
        auto_continues = 0
        while True:
            stop = _SegmentStop()
            result = Runner.run_streamed(agent, input_items, context=ctx, max_turns=AGENT_TASK_MAX_TURNS, run_config=run_config())
            monitor = asyncio.create_task(_monitor(result, attempt, ctx, stop, started))
            try:
                last_text = await _stream(result, recorder) or last_text
            except MaxTurnsExceeded:
                stop.set("max_turns")
            finally:
                monitor.cancel()
            for key, value in _usage(result).items():
                usage[key] += value

            if stop.reason in STOP_MESSAGES:
                canceled = stop.reason == "canceled"
                await asyncio.to_thread(state.finalize_task, attempt, status="canceled" if canceled else "failed",
                                        summary=STOP_MESSAGES[stop.reason] if canceled else "",
                                        detail=_result_detail(task_id, target, ctx, usage, last_text, stop_reason=stop.reason),
                                        error="" if canceled else STOP_MESSAGES[stop.reason])
                return
            if ctx.pending_question:
                await _park(attempt, result, RUNTIME_WAITING_INPUT, ctx.pending_question, usage)
                return
            if stop.reason == "max_turns" and auto_continues < AGENT_TASK_AUTO_CONTINUE_LIMIT:
                # Keep the slot and the context: a fresh turn budget plus a
                # nudge to commit beats stranding the task on a "继续" button.
                auto_continues += 1
                note = _continue_note(auto_continues, AGENT_TASK_AUTO_CONTINUE_LIMIT)
                await recorder.emit("decision", note, {"decision": note, "system": True, "auto_continue": auto_continues})
                input_items = result.to_input_list() + [BUDGET_NUDGE]
                continue
            if stop.reason:  # pause / timeout / max_turns (auto-continues exhausted)
                note = AUTO_PAUSE_NOTES.get(stop.reason)
                if note:
                    await recorder.emit("decision", note, {"decision": note, "system": True})
                await _park(attempt, result, RUNTIME_PAUSED, None, usage)
                return

            final_text = str(result.final_output or "").strip()
            more = await asyncio.to_thread(state.take_pending_supplements, task_id)
            if more and round_index < MAX_SUPPLEMENT_ROUNDS:
                round_index += 1
                input_items = result.to_input_list() + [supplement_message(more)]
                last_text = final_text or last_text
                continue
            await asyncio.to_thread(state.mark_finalizing, task_id)
            await recorder.emit("usage", f"模型调用 {usage['requests']} 次", usage)
            if not final_text and not collect_task_workspace_artifacts(task_id):
                await asyncio.to_thread(state.finalize_task, attempt, status="failed", summary="",
                                        detail=_result_detail(task_id, target, ctx, usage, last_text),
                                        error="Agent 没有给出结果，请补充说明后重试。")
                return
            await asyncio.to_thread(state.finalize_task, attempt, status="completed",
                                    summary=_summary_line(final_text) or "已生成任务文件。",
                                    detail=_result_detail(task_id, target, ctx, usage, final_text))
            return
    except Exception as exc:  # model/runtime failure: keep partial results
        print(f"[AGENT_SDK] task {task_id} failed: {exc!r}")
        await asyncio.to_thread(state.finalize_task, attempt, status="failed", summary="",
                                detail=_result_detail(task_id, target, ctx, usage, last_text,
                                                      partial_result_available=bool(last_text or ctx.operations)),
                                error=_model_error_message(exc))
    finally:
        await bridge.close()
        await client.close()


async def _park(attempt: dict[str, Any], result: Any, reason: str, question: dict[str, Any] | None, usage: dict[str, int]) -> None:
    await _park_items(attempt, result.to_input_list(), reason, question, usage)


async def _park_items(attempt: dict[str, Any], history: list[Any], reason: str, question: dict[str, Any] | None,
                      usage: dict[str, int]) -> None:
    try:
        outcome = await asyncio.to_thread(state.park_task, attempt, reason=reason, history=history,
                                          question=question, usage=usage)
    except Exception as exc:
        print(f"[AGENT_SDK] park failed for task {attempt['task_id']}: {exc!r}")
        await asyncio.to_thread(state.finalize_task, attempt, status="failed", summary="",
                                detail={"provider": AGENT_RUNTIME_PROVIDER}, error=f"无法保存进度：{clip(exc, 200)}")
        return
    if outcome == "canceled":
        await asyncio.to_thread(state.finalize_task, attempt, status="canceled", summary="任务已取消。",
                                detail={"provider": AGENT_RUNTIME_PROVIDER})
