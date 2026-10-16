from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _build_response_json(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("new_message") or "").strip()
    response_format = str(payload.get("response_format") or "").strip().lower()
    task_label = str(payload.get("task_label") or "").strip()
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []

    if tools:
        view = ""
        params: dict[str, Any] = {}
        if "没交" in prompt or "提交" in prompt:
            view = "assignment_submission_status"
        elif "成绩" in prompt or "分数" in prompt or "低于" in prompt or "不及格" in prompt:
            view = "low_scores"
        elif "日程" in prompt or "课表" in prompt or "监考" in prompt:
            view = "my_schedule"
        elif "课堂" in prompt or "哪些班" in prompt:
            view = "my_classrooms"
        elif "名单" in prompt or "人数" in prompt or "学生" in prompt or "多少" in prompt:
            view = "class_roster"
        if "三班" in prompt:
            params["class_keyword"] = "三班"
        if view:
            return {
                "status": "success",
                "response_text": "",
                "tool_calls": [
                    {
                        "id": "mock-platform-query-1",
                        "type": "function",
                        "name": "platform_query",
                        "arguments": {"view": view, "params": params},
                    }
                ],
            }
        return {"status": "success", "response_text": "", "tool_calls": []}

    if response_format == "json":
        if task_label == "lessondoc_editor_proposal":
            try:
                current = json.loads(prompt.split("【当前内容】\n", 1)[1].split("\n【教师要求】\n", 1)[0])
                proposal = copy.deepcopy(current)
                if proposal.get("type") == "text":
                    proposal["md"] = str(proposal.get("md") or "") + "\n\nAI 验收示例：先观察，再验证。"
                elif proposal.get("layout"):
                    proposal = {"layout": "content", "title": proposal.get("title", "AI 改进示例"), "blocks": [{"type": "text", "md": "AI 验收示例：用小步骤分解问题，并验证每一步。"}]}
                return {"status": "success", "response_json": proposal}
            except (ValueError, IndexError, TypeError):
                return {"status": "success", "response_json": {"layout": "content", "blocks": [{"type": "text", "md": "AI 验收示例"}]}}
        if task_label.startswith("knowledge_tree:"):
            def unit(label: str, weight: int) -> dict[str, Any]:
                return {"label": label, "weight": weight, "detail": f"{label}的要点说明（验收示例）。",
                        "example": f"{label}的课堂案例。", "question": f"如何判断{label}已经掌握？",
                        "prerequisite": "先理解上一节的基本概念。", "extension": "为后续内容打基础。"}
            return {
                "status": "success",
                "response_json": {
                    "title": "传输层可靠传输",
                    "summary": "理解确认、重传与滑动窗口如何共同实现可靠传输。",
                    "nodes": [
                        {"label": "可靠传输的基本原理", "detail": "可靠传输需要解决丢失、乱序与重复。", "children": [
                            {"label": "确认与重传", "children": [unit("ACK 确认语义", 8), unit("超时重传机制", 12), unit("快速重传", 6)]},
                            {"label": "序号与去重", "children": [unit("字节流序号", 6), unit("重复报文丢弃", 4)]},
                        ]},
                        {"label": "滑动窗口与流量控制", "children": [
                            {"label": "发送窗口", "children": [unit("窗口大小的含义", 10), unit("窗口滑动过程", 12)]},
                            {"label": "接收窗口通告", "children": [unit("rwnd 通告", 8), unit("零窗口探测", 5)]},
                        ]},
                        {"label": "连接管理", "children": [unit("三次握手", 12), unit("四次挥手", 10), unit("TIME_WAIT 状态", 7)]},
                    ],
                },
            }
        if task_label == "wrong_summary_knowledge_analysis":
            return {
                "status": "success",
                "response_json": {
                    "summary": "DHCP 地址获取与应用层端口是本次最需要回看讲评的知识点。",
                    "knowledge_points": [
                        {
                            "name": "DHCP 地址获取",
                            "mastery": 48,
                            "reason": "错答集中在把 DHCP 与 DNS、ARP 的功能混淆。",
                            "evidence_questions": [1],
                            "risk_level": "danger",
                        },
                        {
                            "name": "应用层端口",
                            "mastery": 72,
                            "reason": "默认端口记忆不稳，HTTP 与常见开发端口混淆。",
                            "evidence_questions": [2],
                            "risk_level": "warning",
                        },
                        {
                            "name": "局域网互通",
                            "mastery": 86,
                            "reason": "大多数学生能解释同网段互通，少量答案缺少验证步骤。",
                            "evidence_questions": [3],
                            "risk_level": "normal",
                        },
                        {
                            "name": "协议分层",
                            "mastery": 100,
                            "reason": "相关题目答题稳定。",
                            "evidence_questions": [4],
                            "risk_level": "mastered",
                        },
                    ],
                    "weak_points": [
                        {
                            "name": "DHCP 地址获取",
                            "issue": "学生主要错在不能区分自动分配地址、域名解析和地址解析三个动作。",
                            "questions": [1],
                            "priority": "high",
                        }
                    ],
                },
            }
        return {
            "status": "success",
            "response_json": {
                "mood_label": "steady",
                "headline": "讨论区有点热起来了",
                "detail": "问题接得上，回应也不慢，课堂气氛比较稳。",
                "summary": "这是用于压测的 mock AI 响应。",
                "outline": [{"level": 1, "title": "压测上下文"}],
                "keywords": ["load-test", "mock-ai"],
                "teaching_value": "验证课堂 AI 相关链路是否可用。",
                "cautions": ["当前为 mock 模式，不代表真实大模型耗时。"],
            },
        }

    reply_text = f"Mock AI 已收到：{prompt[:80] or '空消息'}。这是用于后端压测的稳定回复。"
    return {
        "status": "success",
        "response_text": reply_text,
    }


app = FastAPI(title="Lanshare Mock AI Assistant")


@app.get("/api/internal/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "mock-ai",
    }


_PROVIDER_HEALTH: dict[str, Any] = {
    "deepseek": {"state": "closed", "available": True, "consecutive_failures": 0, "recent_failures": 0, "trips": 0,
                 "cooldown_seconds": 0, "retry_in_seconds": 0, "probe_in_flight": False, "manual": False,
                 "network_suspected": False, "last_error": "", "last_error_kind": "", "last_error_at": "",
                 "last_success_at": "2026-10-15T09:00:00", "last_state_change_at": "", "last_state_reason": "",
                 "failovers_out": 0, "served_as_failover": 0},
    "volcengine": {"state": "closed", "available": True, "consecutive_failures": 0, "recent_failures": 0, "trips": 0,
                   "cooldown_seconds": 0, "retry_in_seconds": 0, "probe_in_flight": False, "manual": False,
                   "network_suspected": False, "last_error": "", "last_error_kind": "", "last_error_at": "",
                   "last_success_at": "2026-10-15T09:00:00", "last_state_change_at": "", "last_state_reason": "",
                   "failovers_out": 0, "served_as_failover": 0},
}


def _provider_health_payload() -> dict[str, Any]:
    return {"status": "ok", "failover_enabled": True, "labels": {"deepseek": "DeepSeek", "volcengine": "豆包（火山引擎）"},
            "enabled_platforms": list(_PROVIDER_HEALTH), "policy": {"failure_threshold": 2, "window_seconds": 300,
            "cooldown_seconds": 60, "max_cooldown_seconds": 600}, "providers": _PROVIDER_HEALTH,
            "degraded": any(item["state"] != "closed" for item in _PROVIDER_HEALTH.values())}


@app.get("/api/internal/provider-health")
async def provider_health() -> dict[str, Any]:
    """Static breaker snapshot so the diagnostics page can be exercised against the mock."""
    return _provider_health_payload()


@app.post("/api/internal/provider-health/{provider}/{action}")
async def provider_health_action(provider: str, action: str) -> dict[str, Any]:
    state = _PROVIDER_HEALTH.get(provider)
    if state is None or action not in {"trip", "reset"}:
        raise HTTPException(404, "unknown provider or action")
    state.update({"state": "open" if action == "trip" else "closed", "manual": action == "trip",
                  "retry_in_seconds": 600 if action == "trip" else 0, "trips": state["trips"] + (action == "trip"),
                  "last_state_reason": "manual trip from diagnostics" if action == "trip" else "manual reset from diagnostics"})
    return _provider_health_payload()


@app.post("/api/ai/generate-assignment")
async def generate_assignment(request: Request) -> JSONResponse:
    payload = await request.json()
    prompt = str(payload.get("prompt") or "").strip()
    return JSONResponse(
        {
            "status": "success",
            "title": "Mock Assignment",
            "requirements_md": (
                "1. 这是 mock AI 生成的作业。\n"
                f"2. 当前提示词：{prompt[:120] or '空提示'}\n"
                "3. 此结果仅用于压测后端业务链路。"
            ),
        }
    )


@app.post("/api/ai/chat")
async def chat(request: Request) -> JSONResponse:
    payload = await request.json()
    return JSONResponse(_build_response_json(payload))


@app.post("/api/ai/chat-stream")
async def chat_stream(request: Request) -> StreamingResponse:
    payload = await request.json()
    prompt = str(payload.get("new_message") or "").strip()
    thinking = "【思考过程开始】先确认上下文与课堂权限。【思考过程结束】"
    answer = f"Mock AI 流式回复：{prompt[:120] or '空消息'}。当前处于压测模式。"
    chunks = [thinking[:16], thinking[16:], answer[:18], answer[18:]]

    async def _stream():
        for chunk in chunks:
            if not chunk:
                continue
            yield chunk
            await asyncio.sleep(0.03)

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")


@app.post("/api/ai/submit-grading-job")
async def submit_grading_job(request: Request) -> JSONResponse:
    payload = await request.json()
    submission_id = int(payload.get("submission_id") or 0)
    callback_url = str(os.getenv("MAIN_APP_CALLBACK_URL") or "").strip()
    delay_ms = int(os.getenv("MOCK_AI_GRADING_DELAY_MS", "300"))
    job_id = str(uuid.uuid4())
    fingerprint = str(payload.get("submission_fingerprint") or "")

    async def _send_callback() -> None:
        if not callback_url or submission_id <= 0:
            return
        await asyncio.sleep(max(delay_ms, 0) / 1000)
        callback_payload = {
            "submission_id": submission_id,
            "status": "graded",
            "score": 88,
            "feedback_md": "P03 mock AI grading completed. The answer is coherent and actionable.",
            "submission_fingerprint": fingerprint,
            "submitted_at": payload.get("submitted_at"),
        }
        if payload.get("appeal_request_id"):
            # Appeal re-grade: echo the appeal identity like ai_assistant does.
            callback_payload.update({
                "appeal_request_id": payload["appeal_request_id"],
                "appeal_regrade_token": fingerprint,
                "prior_issues": ["P03 mock: 前次批改漏看了学生的补充说明"],
                "appeal_response": "P03 mock: 申诉部分成立，已按复核结果调整。",
                "changed": True,
            })
        try:
            # The main app only accepts HMAC-signed grading callbacks.
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from classroom_app.services.internal_callback_auth import signed_callback_request

            body, headers = signed_callback_request(callback_payload)
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(callback_url, content=body, headers=headers)
        except Exception as exc:  # pragma: no cover - mock service diagnostic only
            print(f"[MOCK_AI] grading callback failed for submission {submission_id}: {exc}")

    asyncio.create_task(_send_callback())
    return JSONResponse(
        {
            "status": "queued",
            "job_id": job_id,
            "submission_id": submission_id,
        }
    )


def main() -> int:
    host = os.getenv("AI_HOST", "127.0.0.1")
    port = int(os.getenv("AI_PORT", 8001))
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
