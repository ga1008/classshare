from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


AI_TASK_FAST_TEXT = "fast_text_response"
AI_TASK_DEEP_TEXT = "deep_text_reasoning"
AI_TASK_LIGHT_MULTIMODAL = "light_multimodal_understanding"
AI_TASK_DEEP_MULTIMODAL = "deep_multimodal_reasoning"
AI_TASK_VISION_OCR = "vision_ocr"
AI_TASK_VISION_INTERACTIVE = "vision_interactive"
AI_TASK_DOCUMENT_MULTIMODAL = "document_multimodal_understanding"
AI_TASK_MULTIMODAL_GRADING = "multimodal_grading"
AI_TASK_MULTIMODAL_ADJUDICATION = "multimodal_adjudication"


@dataclass(frozen=True)
class AITaskPolicy:
    task_type: str
    capability: str
    route_group: str
    quality_tier: str
    description: str


TASK_POLICIES: dict[str, AITaskPolicy] = {
    AI_TASK_FAST_TEXT: AITaskPolicy(
        AI_TASK_FAST_TEXT,
        "standard",
        "text_fast",
        "fast",
        "普通文本回复；保持 DeepSeek 快速模型链路",
    ),
    AI_TASK_DEEP_TEXT: AITaskPolicy(
        AI_TASK_DEEP_TEXT,
        "thinking",
        "text_deep",
        "deep",
        "深度文本推理；保持 DeepSeek 深度模型链路",
    ),
    AI_TASK_LIGHT_MULTIMODAL: AITaskPolicy(
        AI_TASK_LIGHT_MULTIMODAL,
        "vision",
        "multimodal_light",
        "fast",
        "兼容旧调用的轻量多模态理解",
    ),
    AI_TASK_VISION_OCR: AITaskPolicy(
        AI_TASK_VISION_OCR,
        "vision",
        "multimodal_light",
        "fast",
        "OCR、验证码和客观视觉字段提取",
    ),
    AI_TASK_VISION_INTERACTIVE: AITaskPolicy(
        AI_TASK_VISION_INTERACTIVE,
        "vision",
        "multimodal_light",
        "fast",
        "课堂聊天和讨论区的交互式看图问答",
    ),
    AI_TASK_DEEP_MULTIMODAL: AITaskPolicy(
        AI_TASK_DEEP_MULTIMODAL,
        "vision",
        "multimodal_deep",
        "deep",
        "兼容旧调用的深度多模态推理",
    ),
    AI_TASK_DOCUMENT_MULTIMODAL: AITaskPolicy(
        AI_TASK_DOCUMENT_MULTIMODAL,
        "vision",
        "multimodal_deep",
        "deep",
        "教案、考核计划、评学表、简历和材料的文档理解",
    ),
    AI_TASK_MULTIMODAL_GRADING: AITaskPolicy(
        AI_TASK_MULTIMODAL_GRADING,
        "vision",
        "multimodal_grading",
        "grading",
        "学生作业的证据化多模态评分",
    ),
    AI_TASK_MULTIMODAL_ADJUDICATION: AITaskPolicy(
        AI_TASK_MULTIMODAL_ADJUDICATION,
        "vision",
        "multimodal_adjudication",
        "adjudication",
        "仅在低置信度或证据冲突时执行的高质量仲裁",
    ),
}

AI_TASK_TYPES = frozenset(TASK_POLICIES)
TEXT_TASK_TYPES = frozenset({AI_TASK_FAST_TEXT, AI_TASK_DEEP_TEXT})
MULTIMODAL_TASK_TYPES = frozenset(AI_TASK_TYPES - TEXT_TASK_TYPES)

LEGACY_CAPABILITY_TASK_TYPE = {
    "standard": AI_TASK_FAST_TEXT,
    "thinking": AI_TASK_DEEP_TEXT,
    "vision": AI_TASK_DEEP_MULTIMODAL,
}

AI_TASK_TYPE_ALIASES = {
    "standard": AI_TASK_FAST_TEXT,
    "thinking": AI_TASK_DEEP_TEXT,
    "text": AI_TASK_FAST_TEXT,
    "fast_text": AI_TASK_FAST_TEXT,
    "quick_text": AI_TASK_FAST_TEXT,
    "deep_text": AI_TASK_DEEP_TEXT,
    "reasoning_text": AI_TASK_DEEP_TEXT,
    "exam_generation": AI_TASK_DEEP_TEXT,
    "assignment_generation": AI_TASK_DEEP_TEXT,
    "text_grading": AI_TASK_DEEP_TEXT,
    "vision": AI_TASK_DEEP_MULTIMODAL,
    "multimodal": AI_TASK_LIGHT_MULTIMODAL,
    "vision_light": AI_TASK_VISION_OCR,
    "light_vision": AI_TASK_VISION_OCR,
    "ocr": AI_TASK_VISION_OCR,
    "vision_ocr": AI_TASK_VISION_OCR,
    "vision_deep": AI_TASK_DEEP_MULTIMODAL,
    "deep_vision": AI_TASK_DEEP_MULTIMODAL,
    "light_multimodal": AI_TASK_LIGHT_MULTIMODAL,
    "deep_multimodal": AI_TASK_DEEP_MULTIMODAL,
    "document_vision": AI_TASK_DOCUMENT_MULTIMODAL,
    "multimodal_document": AI_TASK_DOCUMENT_MULTIMODAL,
    "grading_vision": AI_TASK_MULTIMODAL_GRADING,
}


ROUTE_GROUP_ENV = {
    "text_fast": "AI_TEXT_FAST_PRIORITY",
    "text_deep": "AI_TEXT_DEEP_PRIORITY",
    "multimodal_light": "AI_MULTIMODAL_LIGHT_PRIORITY",
    "multimodal_deep": "AI_MULTIMODAL_DEEP_PRIORITY",
    "multimodal_grading": "AI_MULTIMODAL_GRADING_PRIORITY",
    "multimodal_adjudication": "AI_MULTIMODAL_ADJUDICATION_PRIORITY",
}

ROUTE_GROUP_DEFAULTS = {
    # Text remains DeepSeek-first. Volcengine is retained only as the existing
    # availability spillover; Qwen/GLM never enter text routing by default.
    "text_fast": "deepseek,volcengine",
    "text_deep": "deepseek,volcengine",
    # Benchmark-backed multimodal balance: Qwen is primary, Doubao is fallback.
    # GLM Flash is last-resort for light recognition only, not an authoritative scorer.
    "multimodal_light": "qwen,volcengine,zhipu",
    "multimodal_deep": "qwen,volcengine,zhipu",
    "multimodal_grading": "qwen,volcengine",
    "multimodal_adjudication": "volcengine,qwen",
}


def normalize_ai_task_type(task_type: str | None, capability: str = "standard") -> str:
    normalized = str(task_type or "").strip().lower()
    if normalized in TASK_POLICIES:
        return normalized
    if normalized in AI_TASK_TYPE_ALIASES:
        return AI_TASK_TYPE_ALIASES[normalized]
    return LEGACY_CAPABILITY_TASK_TYPE.get(capability, AI_TASK_FAST_TEXT)


def task_policy(task_type: str | None, capability: str = "standard") -> AITaskPolicy:
    return TASK_POLICIES[normalize_ai_task_type(task_type, capability)]


def capability_for_task_type(task_type: str, fallback: str = "standard") -> str:
    policy = TASK_POLICIES.get(task_type)
    return policy.capability if policy else fallback


def _parse_provider_order(raw_value: str) -> list[str]:
    providers: list[str] = []
    seen: set[str] = set()
    for raw in str(raw_value or "").split(","):
        provider = raw.strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        providers.append(provider)
    return providers


def provider_order_for_task(
    task_type: str | None,
    capability: str = "standard",
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    env = environ if environ is not None else os.environ
    policy = task_policy(task_type, capability)
    env_name = ROUTE_GROUP_ENV[policy.route_group]
    default = ROUTE_GROUP_DEFAULTS[policy.route_group]

    # AI_PLATFORM_PRIORITY remains the backward-compatible text default.
    if policy.route_group.startswith("text_"):
        raw = env.get(env_name) or env.get("AI_PLATFORM_PRIORITY") or default
    else:
        raw = env.get(env_name) or default
    return _parse_provider_order(raw)


def configured_provider_order(environ: Mapping[str, str] | None = None) -> list[str]:
    """Return a stable union of providers referenced by any task policy."""
    result: list[str] = []
    seen: set[str] = set()
    for policy in TASK_POLICIES.values():
        for provider in provider_order_for_task(policy.task_type, policy.capability, environ=environ):
            if provider in seen:
                continue
            seen.add(provider)
            result.append(provider)
    return result


def public_policy_snapshot(environ: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """Safe health/debug representation. It intentionally contains no keys or base URLs."""
    return [
        {
            "task_type": policy.task_type,
            "capability": policy.capability,
            "route_group": policy.route_group,
            "quality_tier": policy.quality_tier,
            "providers": provider_order_for_task(
                policy.task_type,
                policy.capability,
                environ=environ,
            ),
        }
        for policy in TASK_POLICIES.values()
    ]
