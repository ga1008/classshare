from __future__ import annotations

import asyncio
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _build_response_json(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("new_message") or "").strip()
    response_format = str(payload.get("response_format") or "").strip().lower()

    if response_format == "json":
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


def main() -> int:
    host = os.getenv("AI_HOST", "127.0.0.1")
    port = int(os.getenv("AI_PORT", 8001))
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
