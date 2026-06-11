from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from classroom_app.services.chat_platform_query_service import (
    MAX_PLATFORM_TOOL_CALLS,
    detect_platform_query_tool_calls,
    infer_platform_query_tool_calls,
    run_platform_view,
)

TARGET_ACCURACY = 0.85
TARGET_P95_MS = 8000


@dataclass(frozen=True)
class LightQueryEvalCase:
    id: str
    message: str
    expected_views: tuple[str, ...]
    expect_needs_agent: bool = False


def light_query_eval_cases() -> tuple[LightQueryEvalCase, ...]:
    """G9 验收用 20 个轻查询样例，覆盖最高频白名单视图与转 Agent 边界。"""
    return (
        LightQueryEvalCase("assignment_missing_class", "三班这次作业有多少人没交？", ("assignment_submission_status",)),
        LightQueryEvalCase("assignment_submit_rate", "帮我看一下最近一次作业提交情况", ("assignment_submission_status",)),
        LightQueryEvalCase("assignment_submitted", "综合英语作业交了多少人？", ("assignment_submission_status",)),
        LightQueryEvalCase("assignment_missing_list", "列出三班未交作业的学生名单", ("assignment_submission_status",)),
        LightQueryEvalCase("roster_count", "三班有多少学生？", ("class_roster",)),
        LightQueryEvalCase("roster_names", "帮我查三班学生名单", ("class_roster",)),
        LightQueryEvalCase("roster_all", "我的班级花名册有哪些？", ("class_roster",)),
        LightQueryEvalCase("roster_combo", "三班人数和学生名单一起给我", ("class_roster",)),
        LightQueryEvalCase("low_scores_default", "最近有哪些学生不及格？", ("low_scores",)),
        LightQueryEvalCase("low_scores_threshold", "帮我列出低于 72 分的学生名单", ("low_scores",)),
        LightQueryEvalCase("low_scores_class", "三班成绩低于 60 分的是谁？", ("low_scores",)),
        LightQueryEvalCase("low_scores_pass", "哪些学生分数没及格？", ("low_scores",)),
        LightQueryEvalCase("schedule_week", "我未来一周有什么日程安排？", ("my_schedule",)),
        LightQueryEvalCase("schedule_exam", "最近有考试或监考安排吗？", ("my_schedule",)),
        LightQueryEvalCase("schedule_classes", "这周课表安排帮我看一下", ("my_schedule",)),
        LightQueryEvalCase("schedule_invigilation", "我的监考日程有哪些？", ("my_schedule",)),
        LightQueryEvalCase("my_classrooms", "我现在有哪些课堂？", ("my_classrooms",)),
        LightQueryEvalCase("my_classes", "我带了哪些班？", ("my_classrooms",)),
        LightQueryEvalCase(
            "two_round_assignment_and_roster",
            "三班人数和这次作业没交名单一起查一下",
            ("assignment_submission_status", "class_roster"),
        ),
        LightQueryEvalCase(
            "handoff_three_views",
            "深入分析三班提交情况、低分学生和本周日程，给我一个综合判断",
            ("assignment_submission_status", "low_scores"),
            expect_needs_agent=True,
        ),
    )


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
    return float(ordered[index])


def _planned_views(plan: dict[str, Any]) -> list[str]:
    calls = plan.get("tool_calls") if isinstance(plan.get("tool_calls"), list) else []
    return [str(call.get("view") or "") for call in calls if isinstance(call, dict)]


def _case_passed(case: LightQueryEvalCase, plan: dict[str, Any], execution_errors: list[str]) -> bool:
    views = _planned_views(plan)
    executable_views = views[:MAX_PLATFORM_TOOL_CALLS]
    expected_prefix = list(case.expected_views[:MAX_PLATFORM_TOOL_CALLS])
    if expected_prefix and executable_views[: len(expected_prefix)] != expected_prefix:
        return False
    if case.expect_needs_agent and not bool(plan.get("needs_agent")):
        return False
    if not case.expect_needs_agent and len(views) <= MAX_PLATFORM_TOOL_CALLS and bool(plan.get("needs_agent")):
        return False
    return not execution_errors


async def _plan_case(case: LightQueryEvalCase, planner: str) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    if planner == "ai":
        plan = await detect_platform_query_tool_calls(case.message)
    elif planner == "local":
        plan = infer_platform_query_tool_calls(case.message)
    else:
        raise ValueError("planner must be 'local' or 'ai'.")
    elapsed_ms = (time.perf_counter() - start) * 1000
    return plan, elapsed_ms


async def run_light_query_eval_async(
    *,
    planner: str = "local",
    cases: Sequence[LightQueryEvalCase] | None = None,
    conn: Any | None = None,
    teacher_id: int | None = None,
    execute_views: bool = False,
    min_accuracy: float = TARGET_ACCURACY,
    max_p95_ms: int = TARGET_P95_MS,
) -> dict[str, Any]:
    selected_cases = tuple(cases or light_query_eval_cases())
    results: list[dict[str, Any]] = []
    elapsed_values: list[float] = []
    for case in selected_cases:
        plan, elapsed_ms = await _plan_case(case, planner)
        elapsed_values.append(elapsed_ms)
        execution_errors: list[str] = []
        executed_views: list[str] = []
        if execute_views:
            if conn is None or teacher_id is None:
                raise ValueError("execute_views requires conn and teacher_id.")
            for call in (plan.get("tool_calls") or [])[:MAX_PLATFORM_TOOL_CALLS]:
                if not isinstance(call, dict):
                    continue
                view = str(call.get("view") or "")
                try:
                    result = run_platform_view(
                        conn,
                        teacher_id=int(teacher_id),
                        view=view,
                        params=call.get("params") or {},
                    )
                    executed_views.append(f"{view}:{len(result.get('rows') or [])}")
                except Exception as exc:  # noqa: BLE001 - eval reports every failure.
                    execution_errors.append(f"{view}: {exc}")
        passed = _case_passed(case, plan, execution_errors)
        results.append(
            {
                "id": case.id,
                "message": case.message,
                "expected_views": list(case.expected_views),
                "expect_needs_agent": case.expect_needs_agent,
                "planned_views": _planned_views(plan),
                "needs_agent": bool(plan.get("needs_agent")),
                "latency_ms": round(elapsed_ms, 2),
                "executed_views": executed_views,
                "execution_errors": execution_errors,
                "success": passed,
                "plan": plan,
                "case": asdict(case),
            }
        )
    success_count = sum(1 for item in results if item["success"])
    case_count = len(results)
    accuracy = (success_count / case_count) if case_count else 0.0
    p95_ms = _p95(elapsed_values)
    return {
        "name": "agent_g9_light_query_eval",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "planner": planner,
        "execute_views": bool(execute_views),
        "case_count": case_count,
        "success_count": success_count,
        "accuracy": accuracy,
        "target_accuracy": float(min_accuracy),
        "p95_ms": round(p95_ms, 2),
        "target_p95_ms": int(max_p95_ms),
        "passed": accuracy >= float(min_accuracy) and p95_ms <= int(max_p95_ms),
        "results": results,
    }


def run_light_query_eval(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_light_query_eval_async(**kwargs))


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run G9 chat platform-query light-query evaluation.")
    parser.add_argument("--planner", choices=("local", "ai"), default="local")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument("--min-accuracy", type=float, default=TARGET_ACCURACY)
    parser.add_argument("--max-p95-ms", type=int, default=TARGET_P95_MS)
    args = parser.parse_args(argv)

    report = run_light_query_eval(
        planner=args.planner,
        min_accuracy=args.min_accuracy,
        max_p95_ms=args.max_p95_ms,
    )
    if args.output:
        _write_report(args.output, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "G9 light-query eval: "
            f"{report['success_count']}/{report['case_count']} "
            f"({report['accuracy']:.0%}); p95={report['p95_ms']:.0f}ms; "
            f"targets accuracy>={float(args.min_accuracy):.0%}, p95<={int(args.max_p95_ms)}ms; "
            f"passed={report['passed']}"
        )
        if args.output:
            print(f"Report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
