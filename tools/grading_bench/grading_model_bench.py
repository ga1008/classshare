"""Grading model benchmark: replay real production grading jobs against several models.

Usage (from repo root):
    python tools/experiments/grading_model_bench.py --data-dir <dir> [--providers ...] [--subs ...]

The data dir must contain payloads.json (one JSON object per line, last line wins) produced by
dumping the app-side grading job payloads, plus the submission files extracted under
<data-dir>/lanshare/data/files/... (mirrors /app/data/files/... inside the containers).
Results are written to <data-dir>/results/*.json and never touch the production system.

Unlike tools/ai_multimodal_grading_benchmark.py (anonymized dataset + simplified prompt), this
script reuses the exact production pipeline from ai_assistant: message building, JSON parsing,
coverage validation and deterministic objective-question overrides.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from openai import AsyncOpenAI  # noqa: E402

import ai_assistant as A  # noqa: E402
from classroom_app.services.ai_model_policy import (  # noqa: E402
    AI_TASK_DEEP_TEXT,
    AI_TASK_MULTIMODAL_GRADING,
)

MAX_OUTPUT_TOKENS = 16384
TIMEOUT_SECONDS = 600.0
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
THINKING_ON = {"thinking": {"type": "enabled"}}
JSON_OBJECT = {"type": "json_object"}

PROVIDERS: dict[str, dict[str, Any]] = {
    "doubao_pro": {
        "platform": "volcengine", "type": "volcengine", "env": "ARK_API_KEY", "base_url": ARK_BASE_URL,
        "model": "doubao-seed-2-1-pro-260628", "stream": False,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "high", "max_completion_tokens": MAX_OUTPUT_TOKENS},
    },
    "doubao_lite": {
        "platform": "volcengine", "type": "volcengine", "env": "ARK_API_KEY", "base_url": ARK_BASE_URL,
        "model": "doubao-seed-2-0-lite-260428", "stream": False,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "high", "max_completion_tokens": MAX_OUTPUT_TOKENS},
    },
    "ds_flash": {
        "platform": "deepseek", "type": "openai", "env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "stream": True,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "max", "max_tokens": MAX_OUTPUT_TOKENS,
                   "response_format": JSON_OBJECT},
    },
    "ds_flash_high": {
        "platform": "deepseek", "type": "openai", "env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "stream": True,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "high", "max_tokens": MAX_OUTPUT_TOKENS,
                   "response_format": JSON_OBJECT},
    },
    "ds_flash_max32k": {
        "platform": "deepseek", "type": "openai", "env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "stream": True,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "max", "max_tokens": 32768,
                   "response_format": JSON_OBJECT},
    },
    "ds_v4_pro": {
        "platform": "deepseek", "type": "openai", "env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro", "stream": True, "text_only": True,
        "kwargs": {"extra_body": THINKING_ON, "reasoning_effort": "max", "max_tokens": MAX_OUTPUT_TOKENS,
                   "response_format": JSON_OBJECT},
    },
    "glm_4_6v": {
        "platform": "zhipu", "type": "openai", "env": "ZHIPU_API_KEY", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6v", "stream": True,
        "kwargs": {"extra_body": THINKING_ON, "max_tokens": MAX_OUTPUT_TOKENS, "response_format": JSON_OBJECT},
    },
    "glm_4_6v_flash": {
        "platform": "zhipu", "type": "openai", "env": "ZHIPU_API_KEY", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6v-flash", "stream": True,
        "kwargs": {"extra_body": THINKING_ON, "max_tokens": MAX_OUTPUT_TOKENS, "response_format": JSON_OBJECT},
    },
    "qwen3_7_plus": {
        "platform": "qwen", "type": "openai", "env": "QIANWEN_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-plus", "stream": True,
        "kwargs": {"extra_body": {"enable_thinking": True}, "max_tokens": MAX_OUTPUT_TOKENS, "response_format": JSON_OBJECT},
    },
    "qwen3_6_flash": {
        "platform": "qwen", "type": "openai", "env": "QIANWEN_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.6-flash", "stream": True,
        "kwargs": {"extra_body": {"enable_thinking": True}, "max_tokens": MAX_OUTPUT_TOKENS, "response_format": JSON_OBJECT},
    },
    "sf_qwen3_vl": {
        "platform": "siliconflow", "type": "openai", "env": "SILICONFLOW_API_KEY", "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-VL-235B-A22B-Thinking", "stream": True,
        "kwargs": {"max_tokens": MAX_OUTPUT_TOKENS},
    },
}


def _load_payloads(data_dir: Path) -> dict[str, dict[str, Any]]:
    lines = [ln for ln in (data_dir / "payloads.json").read_text(encoding="utf-8").splitlines() if ln.strip()]
    payloads = json.loads(lines[-1])
    local_root = data_dir / "lanshare"
    for payload in payloads.values():
        for file_info in payload.get("files") or []:
            stored = str(file_info["stored_path"])
            if stored.startswith("/app/"):
                file_info["stored_path"] = str(local_root / stored[len("/app/"):])
        payload["file_paths"] = [f["stored_path"] for f in payload.get("files") or []]
    return payloads


def _reclassify_binary_files(grading_files: list[dict[str, Any]]) -> None:
    for file_info in [f for f in grading_files if f["category"] == "binary"]:
        try:
            if file_info["path"].stat().st_size <= A.AI_GRADING_MAX_RAW_TEXT_FILE_BYTES:
                text, _ = A._read_text_file_excerpt(file_info["path"])
                sample = text[:2000]
                printable = sum(1 for ch in sample if ch.isprintable() or ch in {"\n", "\r", "\t"})
                if sample and printable / len(sample) > 0.3:
                    file_info["category"] = "text"
        except Exception:  # noqa: BLE001
            pass
        if file_info["category"] == "binary":
            file_info["category"] = "metadata_only"


def _prepare_job(payload: dict[str, Any]) -> tuple[A.GradingJob, dict[str, Any]]:
    job = A.GradingJob(**{k: v for k, v in payload.items() if not k.startswith("_")})
    grading_files = A._normalize_grading_files(job)
    for file_info in grading_files:
        file_info["category"] = A._categorize_grading_file(file_info)
    _reclassify_binary_files(grading_files)
    A._validate_grading_file_limits(grading_files)
    A._pre_extract_documents(grading_files)
    A._validate_grading_file_limits(grading_files)
    has_visual = any(f["category"] in {"image", "document_native"} for f in grading_files)
    evidence = A.build_deterministic_grading_evidence(job.exam_scoring_json, job.answers_json)
    context = {
        "grading_files": grading_files,
        "mode": "vision_messages" if has_visual else "text_messages",
        "task_type": AI_TASK_MULTIMODAL_GRADING if has_visual else AI_TASK_DEEP_TEXT,
        "capability": "vision" if has_visual else "thinking",
        "evidence": evidence,
        "evidence_prompt": A.format_deterministic_evidence_prompt(evidence),
        "image_count": sum(1 for f in grading_files if f["category"] == "image"),
        "expected_question_count": A._grading_expected_question_count(job),
    }
    return job, context


def _messages_for(job: A.GradingJob, ctx: dict[str, Any], platform_type: str) -> list[dict[str, Any]]:
    messages = A._build_grading_chat_messages(
        job=job, grading_files=ctx["grading_files"], execution_mode=ctx["mode"], platform_type=platform_type,
        deterministic_evidence_prompt=ctx["evidence_prompt"],
    )
    return A._prepare_chat_messages_for_platform(messages, capability=ctx["capability"])


async def _call(provider: dict[str, Any], api_key: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    client = AsyncOpenAI(api_key=api_key, base_url=provider["base_url"], timeout=TIMEOUT_SECONDS, max_retries=0)
    kwargs: dict[str, Any] = {"model": provider["model"], "messages": messages, **copy.deepcopy(provider["kwargs"])}
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    first_token_at: float | None = None
    usage = None
    finish_reason = None
    answer_parts: list[str] = []
    thinking_parts: list[str] = []
    try:
        if provider["stream"]:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                usage = A._extract_provider_usage(chunk) or usage
                if getattr(chunk, "choices", None):
                    finish_reason = getattr(chunk.choices[0], "finish_reason", None) or finish_reason
                if not getattr(chunk, "choices", None) or not chunk.choices[0].delta:
                    continue
                reasoning_text, content_text = A._extract_delta_parts(chunk.choices[0].delta)
                if (reasoning_text or content_text) and first_token_at is None:
                    first_token_at = time.perf_counter() - t0
                if reasoning_text:
                    thinking_parts.append(reasoning_text)
                if content_text:
                    answer_parts.append(content_text)
        else:
            completion = await client.chat.completions.create(**kwargs)
            finish_reason = getattr(completion.choices[0], "finish_reason", None)
            message = completion.choices[0].message
            answer_parts.append(A._coerce_stream_text(getattr(message, "content", None)) or "")
            thinking_parts.append(A._extract_reasoning_text(message) or "")
            usage = A._extract_provider_usage(completion)
    finally:
        await client.close()
    elapsed = time.perf_counter() - t0
    return {
        "started_at": started,
        "elapsed_s": round(elapsed, 2),
        "first_token_s": round(first_token_at, 2) if first_token_at else None,
        "finish_reason": finish_reason,
        "usage": usage,
        "content": "".join(answer_parts),
        "thinking": "".join(thinking_parts),
    }


def _cost(provider: dict[str, Any], usage: dict[str, Any] | None, task_type: str, started_at: str) -> dict[str, Any] | None:
    return A._estimate_provider_cost_cny(
        provider["platform"], usage, task_type=task_type, model_name=provider["model"], request_started_at=started_at,
    )


def _postprocess(raw_text: str, job: A.GradingJob, ctx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"parse_error": None, "validation_error": None}
    try:
        parsed = A._robust_parse_grading_json(raw_text)
    except Exception as exc:  # noqa: BLE001
        out["parse_error"] = str(exc)[:300]
        return out
    out["raw_score"] = parsed.get("score")
    out["raw_questions"] = [
        {"question_id": q.get("question_id"), "question_no": q.get("question_no"), "score": q.get("score"),
         "max_score": q.get("max_score"), "deduction_points": q.get("deduction_points"), "evaluation": q.get("evaluation")}
        for q in (parsed.get("questions") or []) if isinstance(q, dict)
    ]
    out["summary"] = parsed.get("summary")
    out["confidence"] = parsed.get("confidence")
    out["needs_review"] = parsed.get("needs_review")
    out["evidence_conflicts"] = parsed.get("evidence_conflicts")
    try:
        validated = A._validate_grading_result_for_job(copy.deepcopy(parsed), job)
        validated = A.apply_deterministic_grading_result(validated, ctx["evidence"])
        final = A.normalize_grading_result(validated, answers_json=job.answers_json)
        out["final_score"] = final.get("score")
        out["final_questions"] = [
            {"question_id": q.get("question_id"), "score": q.get("score"), "max_score": q.get("max_score")}
            for q in (final.get("questions") or []) if isinstance(q, dict)
        ]
    except Exception as exc:  # noqa: BLE001
        out["validation_error"] = str(getattr(exc, "detail", None) or exc)[:400]
    return out


async def _run_one(sem: asyncio.Semaphore, key: str, provider: dict[str, Any], api_key: str, sid: str,
                   job: A.GradingJob, ctx: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    out_path = out_dir / f"{sid}__{key}.json"
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    messages = _messages_for(job, ctx, provider["type"])
    record: dict[str, Any] = {
        "submission_id": sid, "provider_key": key, "model": provider["model"],
        "task_type": ctx["task_type"], "mode": ctx["mode"], "image_count": ctx["image_count"],
    }
    async with sem:
        print(f"[bench] start {sid} {key} ({provider['model']}) imgs={ctx['image_count']}", flush=True)
        try:
            call = await _call(provider, api_key, messages)
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            print(f"[bench] FAIL  {sid} {key}: {record['error']}", flush=True)
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
    record.update({k: v for k, v in call.items() if k not in {"content", "thinking"}})
    record["thinking_chars"] = len(call["thinking"] or "")
    record["content_chars"] = len(call["content"] or "")
    record["cost"] = _cost(provider, call["usage"], ctx["task_type"], call["started_at"])
    record.update(_postprocess(call["content"], job, ctx))
    record["raw_content"] = call["content"]
    cost_value = (record["cost"] or {}).get("estimated_cost")
    print(
        f"[bench] done  {sid} {key}: {record['elapsed_s']}s raw={record.get('raw_score')} final={record.get('final_score')} "
        f"cost={cost_value} err={record.get('parse_error') or record.get('validation_error')}",
        flush=True,
    )
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--providers", nargs="*", default=list(PROVIDERS))
    parser.add_argument("--subs", nargs="*", default=None)
    parser.add_argument("--per-provider-concurrency", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    out_dir = data_dir / "results"
    out_dir.mkdir(exist_ok=True)
    payloads = _load_payloads(data_dir)
    if args.subs:
        payloads = {k: v for k, v in payloads.items() if k in set(args.subs)}
    prepared: dict[str, tuple[A.GradingJob, dict[str, Any]]] = {}
    for sid, payload in payloads.items():
        job, ctx = _prepare_job(payload)
        prepared[sid] = (job, ctx)
        print(
            f"[bench] {sid}: mode={ctx['mode']} files={len(ctx['grading_files'])} images={ctx['image_count']} "
            f"expected_q={ctx['expected_question_count']} evidence={ctx['evidence'].get('available')}",
            flush=True,
        )
    if args.dry_run:
        return
    sems = {name: asyncio.Semaphore(args.per_provider_concurrency) for name in {p["platform"] for p in PROVIDERS.values()}}
    tasks = []
    for key in args.providers:
        provider = PROVIDERS[key]
        api_key = os.getenv(provider["env"]) or ""
        if not api_key:
            print(f"[bench] skip {key}: {provider['env']} not set", flush=True)
            continue
        for sid, (job, ctx) in prepared.items():
            if provider.get("text_only") and ctx["mode"] != "text_messages":
                continue
            tasks.append(_run_one(sems[provider["platform"]], key, provider, api_key, sid, job, ctx, out_dir))
    results = await asyncio.gather(*tasks)
    (data_dir / "results.json").write_text(json.dumps(list(results), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bench] wrote {len(results)} records", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
