"""Model endpoint for the Agent: DeepSeek V4.1 Flash via the OpenAI-compatible API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agents import ModelSettings, OpenAIChatCompletionsModel, RunConfig, set_tracing_disabled
from agents.run_config import CallModelData, ModelInputData
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from ...config import AGENT_MODEL_DEFAULT

# Traces would be exported to OpenAI's platform; the Agent never phones home.
set_tracing_disabled(True)

DEFAULT_BASE_URL = "https://api.deepseek.com"
MODEL_TIMEOUT_SECONDS = 240.0
MAX_OUTPUT_TOKENS = 16384


class AgentModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelTarget:
    model: str
    base_url: str
    api_key: str
    source: str  # "agent_key" (super-admin managed) | "env"


def openai_base_url(value: str | None) -> str:
    """Agent keys may carry the Anthropic-compatible URL used by the old DSH runtime."""
    url = str(value or "").strip().rstrip("/") or DEFAULT_BASE_URL
    for suffix in ("/anthropic/v1", "/anthropic"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url or DEFAULT_BASE_URL


def agent_model_name() -> str:
    """The Agent is multimodal (screenshots, image attachments): only a
    vision-capable DeepSeek model qualifies. A stale DSH-era value such as
    deepseek-v4-pro falls back to deepseek-flash (V4.1 Flash)."""
    from ..ai_model_policy import DEEPSEEK_FLASH_MODEL, DEEPSEEK_VISION_MODELS

    configured = (AGENT_MODEL_DEFAULT or "").strip()
    return configured if configured in DEEPSEEK_VISION_MODELS else DEEPSEEK_FLASH_MODEL


def resolve_model_target(conn) -> ModelTarget:
    from ..agent_key_service import get_active_agent_api_key

    model = agent_model_name()
    active = get_active_agent_api_key(conn)
    if active:
        row, secret = active
        return ModelTarget(model=model, base_url=openai_base_url(row.get("base_url")), api_key=secret, source="agent_key")
    secret = str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not secret:
        raise AgentModelUnavailable("Agent 模型密钥未配置：请超级管理员在系统设置中配置 Agent API Key。")
    return ModelTarget(model=model, base_url=openai_base_url(os.getenv("DEEPSEEK_BASE_URL")), api_key=secret, source="env")


def build_model(target: ModelTarget) -> tuple[OpenAIChatCompletionsModel, AsyncOpenAI]:
    client = AsyncOpenAI(api_key=target.api_key, base_url=target.base_url, timeout=MODEL_TIMEOUT_SECONDS, max_retries=2)
    return OpenAIChatCompletionsModel(model=target.model, openai_client=client), client


def _is_empty_assistant_message(item: Any) -> bool:
    data = item.model_dump(exclude_unset=True) if hasattr(item, "model_dump") else item
    if not isinstance(data, dict) or data.get("role") != "assistant" or data.get("type", "message") != "message":
        return False
    content = data.get("content")
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return all(isinstance(part, dict) and part.get("type") in ("output_text", "text")
                   and not str(part.get("text") or "").strip() for part in content)
    return False


def deepseek_input_filter(data: CallModelData[Any]) -> ModelInputData:
    """DeepSeek rejects an assistant message wedged between tool_calls and their
    tool results. Streaming yields an empty text item (first delta content "")
    that the SDK replays as exactly that, so empty assistant messages are dropped
    before every model call (verified against the live API 2026-09-25)."""
    items = [item for item in data.model_data.input if not _is_empty_assistant_message(item)]
    return ModelInputData(input=items, instructions=data.model_data.instructions)


def run_config() -> RunConfig:
    return RunConfig(call_model_input_filter=deepseek_input_filter, tracing_disabled=True)


def model_settings(*, deep_thinking: bool) -> ModelSettings:
    # DeepSeek thinking mode; the SDK replays reasoning_content across tool turns.
    # DeepSeek accepts effort "max", which the OpenAI type's Literal omits, so
    # the value is constructed without validation and sent as reasoning_effort.
    return ModelSettings(
        parallel_tool_calls=True,
        max_tokens=MAX_OUTPUT_TOKENS,
        include_usage=True,
        extra_body={"thinking": {"type": "enabled"}},
        reasoning=Reasoning.model_construct(effort="max" if deep_thinking else "high"),
    )
