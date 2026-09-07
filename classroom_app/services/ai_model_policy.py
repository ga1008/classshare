from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping


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
    "text_fast": "deepseek",
    "text_deep": "deepseek",
    "multimodal_light": "volcengine",
    "multimodal_deep": "volcengine",
    "multimodal_grading": "volcengine",
    "multimodal_adjudication": "volcengine",
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
    # Old deployment variables cannot silently restore cross-provider grading
    # or paid text spillover. Web search has its own explicit tool route.
    allowed = {"deepseek"} if policy.route_group.startswith("text_") else {"volcengine"}
    return [provider for provider in _parse_provider_order(raw) if provider in allowed]


AI_EXECUTION_POLICY_VERSION = "business-routing-2026-09-v2"
DOUBAO_PRO_MODEL = "doubao-seed-2-1-pro-260628"
DOUBAO_LITE_MODEL = "doubao-seed-2-0-lite-260428"
ASSESSMENT_KINDS = frozenset({"homework", "midterm", "final", "legacy_unknown"})
EDGE_FEATURES = frozenset({"chat", "classroom_chat", "discussion", "private_message", "blog", "ocr"})
OPERATIONS = frozenset({"grading", "adjudication", "generation", "chat", "document", "ocr"})


@dataclass(frozen=True)
class AIBusinessContext:
    operation: str = ""
    source_feature: str = ""
    assessment_kind: str | None = None
    intended_assessment_kind: str | None = None
    classification_source: str = ""
    classification_status: str = ""
    assessment_kind_version: int = 0
    assignment_id: int | None = None
    class_offering_id: int | None = None
    job_id: str = ""
    logical_call_id: str = ""
    expected_question_count: int | None = None
    policy_version: str = AI_EXECUTION_POLICY_VERSION

    @classmethod
    def from_mapping(cls, value: Any = None) -> "AIBusinessContext":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("AI business context must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("Unsupported AI business context fields: " + ", ".join(sorted(unknown)))
        fields = dict(value)
        for field in ("operation", "source_feature", "classification_source", "classification_status", "job_id", "logical_call_id"):
            fields[field] = str(fields.get(field) or "").strip()[:160]
        if fields["operation"] and fields["operation"] not in OPERATIONS:
            raise ValueError("Unsupported AI business operation")
        for field in ("assessment_kind", "intended_assessment_kind"):
            fields[field] = str(fields.get(field) or "").strip() or None
            if fields[field] is not None and fields[field] not in ASSESSMENT_KINDS:
                raise ValueError("Unsupported AI assessment kind")
        for field in ("assignment_id", "class_offering_id"):
            if fields.get(field) is not None:
                fields[field] = int(fields[field])
                if fields[field] <= 0:
                    raise ValueError("AI business resource id must be positive")
        fields["assessment_kind_version"] = max(0, int(fields.get("assessment_kind_version") or 0))
        count = fields.get("expected_question_count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000):
            raise ValueError("Expected question count must be a positive integer")
        fields["policy_version"] = str(fields.get("policy_version") or AI_EXECUTION_POLICY_VERSION)
        if fields["policy_version"] != AI_EXECUTION_POLICY_VERSION:
            raise ValueError("Unsupported AI execution policy version")
        return cls(**fields)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AIExecutionPlan:
    profile_id: str
    provider: str
    model: str
    capability: str
    task_type: str
    operation: str
    thinking_type: str
    reasoning_effort: str | None
    max_output_tokens_total: int
    api_style: str = "chat_completions"
    policy_version: str = AI_EXECUTION_POLICY_VERSION
    quality_floor: str = "standard"
    allowed_fallbacks: tuple[str, ...] = ()
    output_schema: str = ""
    expected_question_count: int | None = None
    output_size_tier: str = ""

    @property
    def route_id(self) -> str:
        return ":".join((self.profile_id, self.provider, self.model, self.reasoning_effort or "none", str(self.max_output_tokens_total), self.api_style, self.policy_version))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "allowed_fallbacks": list(self.allowed_fallbacks), "route_id": self.route_id}

    def for_api(self, api_style: str) -> "AIExecutionPlan":
        if api_style not in {"chat_completions", "responses"}:
            raise ValueError("Unsupported AI API style")
        return replace(self, api_style=api_style)


def _operation_for_task(task_type: str) -> str:
    if task_type == AI_TASK_MULTIMODAL_GRADING:
        return "grading"
    if task_type == AI_TASK_MULTIMODAL_ADJUDICATION:
        return "adjudication"
    if task_type == AI_TASK_VISION_OCR:
        return "ocr"
    if task_type in {AI_TASK_DOCUMENT_MULTIMODAL, AI_TASK_DEEP_MULTIMODAL}:
        return "document"
    return "chat"


def resolve_execution_plan(
    task_type: str | None,
    capability: str = "standard",
    business_context: AIBusinessContext | Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    execution_snapshot: Mapping[str, Any] | None = None,
) -> AIExecutionPlan:
    """Resolve only trusted business facts; titles and prompt text never select tiers."""
    env = os.environ if environ is None else environ
    context = AIBusinessContext.from_mapping(business_context)
    task = normalize_ai_task_type(task_type, capability)
    operation = context.operation or _operation_for_task(task)
    visual = task in MULTIMODAL_TASK_TYPES
    if not visual:
        deep = task == AI_TASK_DEEP_TEXT
        profile = "text_deep" if deep else "text_fast"
        default_model = "deepseek-v4-pro" if deep else "deepseek-v4-flash"
        model = env.get("DEEPSEEK_MODEL_DEEP_TEXT" if deep else "DEEPSEEK_MODEL_FAST_TEXT") or env.get("DEEPSEEK_MODEL_THINKING" if deep else "DEEPSEEK_MODEL_STANDARD") or default_model
        if model not in {"deepseek-v4-pro", "deepseek-v4-flash"}:
            raise ValueError("Text model is outside the verified DeepSeek allowlist")
        effort = ("max" if operation in {"grading", "adjudication", "generation"} else "high") if deep else None
        plan = AIExecutionPlan(profile, "deepseek", model, "thinking" if deep else "standard", task, operation, "enabled" if deep else "disabled", effort, 16384 if deep else 4096)
    else:
        kind = context.assessment_kind or context.intended_assessment_kind
        edge = operation in {"chat", "ocr"} or context.source_feature in EDGE_FEATURES
        if edge and operation in {"grading", "adjudication"}:
            raise ValueError("An edge feature cannot perform authoritative grading")
        high = not edge and (
            operation == "adjudication"
            or context.source_feature == "personal_stage"
            or (operation in {"grading", "generation"} and kind in {"midterm", "final"})
            or (operation == "grading" and kind in {None, "legacy_unknown"})
        )
        profile = "vision_edge_low" if edge else ("vision_assessment_high" if high else "vision_pro_low")
        model = env.get("AI_VISION_LITE_MODEL" if edge else "AI_VISION_PRO_MODEL") or (DOUBAO_LITE_MODEL if edge else DOUBAO_PRO_MODEL)
        if model != (DOUBAO_LITE_MODEL if edge else DOUBAO_PRO_MODEL):
            raise ValueError("Vision model is outside the business operation allowlist")
        plan = AIExecutionPlan(profile, "volcengine", model, "vision", task, operation, "enabled", "high" if high else "low", 4096 if edge else (16384 if high else 8192), quality_floor="assessment" if high else ("grading" if operation == "grading" else "standard"))
    limit_name = "AI_PROFILE_" + plan.profile_id.upper() + "_MAX_OUTPUT_TOKENS"
    raw_limit = env.get(limit_name)
    if raw_limit:
        limit = int(raw_limit)
        if not 1 <= limit <= 32768:
            raise ValueError("AI profile output limit must be between 1 and 32768")
        plan = replace(plan, max_output_tokens_total=limit)
    if execution_snapshot:
        immutable = ("profile_id", "provider", "model", "capability", "operation", "thinking_type", "reasoning_effort", "policy_version", "quality_floor")
        for field in immutable:
            if execution_snapshot.get(field) != getattr(plan, field):
                raise ValueError("AI execution snapshot is incompatible with trusted business context")
        limit = int(execution_snapshot.get("max_output_tokens_total") or 0)
        if not 1 <= limit <= 32768:
            raise ValueError("Invalid AI execution snapshot output limit")
        plan = replace(plan, max_output_tokens_total=limit)
        if execution_snapshot.get("output_schema"):
            plan = replace(plan, output_schema=str(execution_snapshot["output_schema"]),
                expected_question_count=execution_snapshot.get("expected_question_count"),
                output_size_tier=str(execution_snapshot.get("output_size_tier") or ""))
    return plan


class AIOutputSizeError(ValueError):
    """The known structured task cannot fit the configured or frozen output tier."""


def structured_output_size_tier(schema: str, question_count: int | None) -> str:
    """Shared pre-enqueue admission check; reads no model configuration or prose."""
    thresholds = {"grading_v1": (20, 40, 80), "exam_generation_v1": (10, 20, 40)}
    if schema not in thresholds:
        raise ValueError("Unsupported structured output schema")
    if question_count is not None and (isinstance(question_count, bool) or not isinstance(question_count, int) or question_count < 1):
        raise ValueError("Expected question count must be a positive integer")
    small, medium, largest = thresholds[schema]
    if question_count is not None and question_count > largest:
        raise AIOutputSizeError(f"本次结构化任务共{question_count}题，单次上限为{largest}题；请人工处理或按完整题组拆分后重试")
    return "unknown" if question_count is None else (
        "base" if question_count <= small else "medium" if question_count <= medium else "large")


def size_structured_execution_plan(plan: AIExecutionPlan, *, schema: str,
    question_count: int | None, execution_snapshot: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None) -> AIExecutionPlan:
    """Use server-validated counts, never task prose, to bound structured output.

    Compact grading gets 20/40/80-question tiers; full question generation gets
    10/20/40. These are admission heuristics, not token-count guarantees.
    Existing sized snapshots survive configuration changes unchanged.
    """
    if plan.operation not in {"grading", "adjudication", "generation"}:
        raise ValueError("Unsupported structured output schema or operation")
    tier = structured_output_size_tier(schema, question_count)
    if execution_snapshot and execution_snapshot.get("output_schema"):
        if (execution_snapshot.get("output_schema") != schema
                or execution_snapshot.get("expected_question_count") != question_count):
            raise AIOutputSizeError("任务题量或输出结构与已冻结计划不一致，请检查后重新发起任务")
        return plan
    env = os.environ if environ is None else environ
    medium_cap = int(env.get("AI_STRUCTURED_OUTPUT_MEDIUM_MAX_TOKENS", "16384"))
    large_cap = int(env.get("AI_STRUCTURED_OUTPUT_LARGE_MAX_TOKENS", "32768"))
    if not 1 <= medium_cap <= large_cap <= 32768:
        raise ValueError("Structured output caps must satisfy 1 <= medium <= large <= 32768")
    required = {"medium": medium_cap, "large": large_cap}.get(tier, plan.max_output_tokens_total)
    if execution_snapshot and required > plan.max_output_tokens_total:
        raise AIOutputSizeError("任务题量超过旧计划已冻结的输出上限，请人工处理或拆分后重新发起，不能自动增加额度")
    return replace(plan, max_output_tokens_total=max(plan.max_output_tokens_total, required),
        output_schema=schema, expected_question_count=question_count, output_size_tier=tier)


class AIExecutionBudgetExceeded(RuntimeError):
    """The logical operation has exhausted its physical inference allowance."""


class AIExecutionStateError(RuntimeError):
    """Execution state could not be safely persisted; never send another request."""


class AIExecutionBudget:
    """Small serial attempt ledger, persisted before sending any model request.

    Pending attempts survive a worker crash and count as potentially billed
    generations. A definite HTTP rejection releases only the generation slot.
    """

    def __init__(self, state=None, *, logical_call_id="", persist=None):
        import copy
        import uuid
        self.state = copy.deepcopy(state or {
            "version": 1, "revision": 0,
            "logical_call_id": logical_call_id or uuid.uuid4().hex,
            "primary_plan": None, "attempts": [],
        })
        if self.state.get("version") != 1 or not isinstance(self.state.get("attempts"), list):
            raise AIExecutionStateError("Unsupported AI execution ledger")
        self.persist = persist
        self.failed_persistence = False
        self.before_dispatch = None

    @property
    def exhausted(self):
        return (self.failed_persistence or bool(self.state.get("legacy_history_unknown")) or len(self.state["attempts"]) >= 3
                or sum(a.get("status") != "rejected" for a in self.state["attempts"]) >= 2)

    def snapshot(self):
        import copy
        return copy.deepcopy(self.state)

    def audit_snapshot(self):
        """Usage logs/callback metadata need attempts, not student result bodies."""
        state = self.snapshot()
        for key in ("primary_result", "review_result", "repair_candidate"):
            if key in state:
                state["has_" + key] = True
                state.pop(key)
        return state

    async def _save(self):
        revision = int(self.state.get("revision") or 0)
        self.state["revision"] = revision + 1
        if self.persist:
            try:
                await self.persist(self.snapshot(), revision)
            except Exception as exc:
                self.failed_persistence = True
                raise AIExecutionStateError("AI execution ledger persistence failed") from exc

    async def begin(self, plan):
        import uuid
        from datetime import datetime, timezone
        if self.exhausted:
            raise AIExecutionBudgetExceeded("AI execution budget exhausted; manual review required")
        if self.state.get("primary_plan") is None:
            self.state["primary_plan"] = dict(plan)
        entry = {
            "attempt_id": uuid.uuid4().hex,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "route_id": plan.get("route_id"), "profile_id": plan.get("profile_id"),
            "provider": plan.get("provider"), "model": plan.get("model"),
            "operation": plan.get("operation"), "task_type": plan.get("task_type"), "api_style": plan.get("api_style"),
            "reasoning_effort": plan.get("reasoning_effort"),
            "max_output_tokens_total": plan.get("max_output_tokens_total"),
            "status": "pending", "usage": None, "usage_known": False,
            "cost_estimate_cny": None, "cost_known": False,
        }
        self.state["attempts"].append(entry)
        await self._save()
        if self.before_dispatch:
            await self.before_dispatch(plan, entry["attempt_id"])
        return entry["attempt_id"]

    async def finish(self, attempt_id, *, usage=None, finish_reason=None, error=None, cost=None):
        from datetime import datetime, timezone
        entry = next(a for a in self.state["attempts"] if a["attempt_id"] == attempt_id)
        status_code = getattr(error, "status_code", None)
        if status_code is None:
            status_code = getattr(getattr(error, "response", None), "status_code", None)
        rejected = status_code in {400, 401, 403, 404, 405, 413, 415, 422, 429}
        entry.update({
            "status": "rejected" if rejected else ("unknown" if error and not usage else "completed"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "finish_reason": finish_reason, "usage": usage,
            "usage_known": bool(usage), "cost_estimate_cny": cost.get("estimated_cost") if isinstance(cost, dict) else cost,
            "cost_basis": cost if isinstance(cost, dict) else None,
            "cost_known": cost is not None,
            "error_code": (error if isinstance(error, str) else error.__class__.__name__) if error else None,
            "http_status": status_code,
        })
        await self._save()


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
