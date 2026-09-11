"""Replay real grading payloads through the production grading pipeline.

Unlike grading_model_bench.py (direct provider calls), this drives
ai_assistant._build_grading_callback_data end to end: business routing, bounded
fallback, result validation/softening and (if quota is reachable) adjudication.

Usage (from repo root, needs provider keys in .env):
    python tools/experiments/replay_grading_pipeline.py --data-dir <dir> --subs 4310 1591 \
        [--env AI_GRADING_STANDARD_PROVIDER=volcengine]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--subs", nargs="*", default=None)
    parser.add_argument("--env", nargs="*", default=[], help="KEY=VALUE applied before ai_assistant is imported")
    return parser.parse_args()


async def _replay(sid: str, payload: dict, out_dir: Path) -> dict:
    import ai_assistant as A

    job = A.GradingJob(**{k: v for k, v in payload.items() if not k.startswith("_")})
    budget = A.AIExecutionBudget(logical_call_id=f"replay-{sid}")
    token = A._active_execution_budget.set(budget)
    started = time.perf_counter()
    try:
        data = await A._build_grading_callback_data(job, raise_on_failure=True)
    except Exception as exc:  # noqa: BLE001
        data = {"status": "exception", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    finally:
        A._active_execution_budget.reset(token)
    elapsed = round(time.perf_counter() - started, 1)
    attempts = [
        {k: a.get(k) for k in ("provider", "model", "profile_id", "operation", "status", "cost_estimate_cny", "usage")}
        for a in budget.state.get("attempts", [])
    ]
    metadata = data.get("execution_metadata") or {}
    fallback = metadata.get("fallback_from")
    record = {
        "submission_id": sid, "elapsed_s": elapsed, "status": data.get("status"), "score": data.get("score"),
        "review_required": data.get("review_required"), "review_reason_codes": data.get("review_reason_codes"),
        "requested_provider": data.get("requested_provider"), "requested_model": data.get("requested_model"),
        "profile_id": metadata.get("profile_id"), "provider": metadata.get("provider"), "model": metadata.get("model"),
        "fallback_from": fallback.get("profile_id") if isinstance(fallback, dict) else fallback,
        "adjudication": (data.get("quality_audit") or {}).get("adjudication"),
        "review_deferred": (data.get("quality_audit") or {}).get("review_deferred"),
        "attempts": attempts, "error": data.get("error"),
        "feedback_head": str(data.get("feedback_md") or "")[:400],
    }
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"replay_{sid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


async def _main() -> None:
    args = _parse_args()
    for item in args.env:
        key, _, value = item.partition("=")
        os.environ[key] = value
    os.environ.setdefault("AI_DURABLE_JOBS_ENABLED", "false")
    os.environ.setdefault("DB_ENGINE", "sqlite")
    from tools.experiments.grading_model_bench import _load_payloads

    data_dir = Path(args.data_dir)
    payloads = _load_payloads(data_dir)
    if args.subs:
        payloads = {k: v for k, v in payloads.items() if k in set(args.subs)}
    for sid, payload in payloads.items():
        record = await _replay(sid, payload, data_dir / "results")
        print(json.dumps({k: v for k, v in record.items() if k != "feedback_head"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
