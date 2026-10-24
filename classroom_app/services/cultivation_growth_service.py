"""Cultivation growth model: concave growth curve + per-signal breakdown.

The four component weights (material / task / interaction / consistency) stay
teacher-adjustable and keep producing a *raw* linear score. This module turns
that raw score into the displayed 修为 with a concave curve so early growth is
fast and later growth slows down, and it explains every point with a list of
fine-grained signals the panel can render.

Pure functions only: no database access, no AI calls.
"""
from __future__ import annotations

import math
import os
from datetime import date
from typing import Any, Iterable

from .cultivation_weight_service import CULTIVATION_WEIGHT_KEYS, CULTIVATION_WEIGHT_LABELS, DEFAULT_CULTIVATION_WEIGHTS

GROWTH_CURVE_EXPONENT_DEFAULT = 1.45
GROWTH_CURVE_EXPONENT_MIN = 1.0
GROWTH_CURVE_EXPONENT_MAX = 2.5
GROWTH_MODEL_VERSION = "growth-v2-2026-10"


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    number = _num(value, low)
    return max(low, min(high, number))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def growth_curve_exponent() -> float:
    """Concavity of the growth curve; env override for operations, clamped."""
    raw = os.environ.get("CULTIVATION_GROWTH_EXPONENT", "").strip()
    if not raw:
        return GROWTH_CURVE_EXPONENT_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        return GROWTH_CURVE_EXPONENT_DEFAULT
    return max(GROWTH_CURVE_EXPONENT_MIN, min(GROWTH_CURVE_EXPONENT_MAX, value))


def apply_growth_curve(raw_score: float, *, exponent: float | None = None) -> float:
    """Map a raw 0-100 score to the displayed 修为 (monotonic, concave).

    score = 100 * (1 - (1 - raw/100) ** k); k > 1 makes early points worth more
    and late points worth less, while 0 -> 0 and 100 -> 100 are preserved.
    """
    k = exponent if exponent is not None else growth_curve_exponent()
    ratio = _clamp(_num(raw_score) / 100)
    return round(100 * (1 - (1 - ratio) ** k), 1)


def raw_score_for(score: float, *, exponent: float | None = None) -> float:
    """Inverse of :func:`apply_growth_curve`: the raw score a display score needs."""
    k = exponent if exponent is not None else growth_curve_exponent()
    ratio = _clamp(_num(score) / 100)
    return round(100 * (1 - (1 - ratio) ** (1 / k)), 1)


def build_stage_effort_table(levels: Iterable[dict[str, Any]], *, exponent: float | None = None) -> list[dict[str, Any]]:
    """Per level: display threshold, raw effort needed, and the raw gap from the previous level.

    The raw gap grows with the tier, which is what makes later stages harder even
    though the display thresholds look evenly spaced.
    """
    rows: list[dict[str, Any]] = []
    previous_raw = 0.0
    for level in levels:
        unlock = _num(level.get("unlock_score"))
        raw_required = raw_score_for(unlock, exponent=exponent)
        rows.append({
            "key": str(level.get("key") or ""),
            "tier": int(_num(level.get("tier"))),
            "unlock_score": unlock,
            "raw_required": raw_required,
            "raw_gap": round(max(0.0, raw_required - previous_raw), 1),
        })
        previous_raw = raw_required
    return rows


# --- signal definitions ----------------------------------------------------

def _share_rows(component: str, rows: list[tuple[str, str, float, str]]) -> list[dict[str, Any]]:
    return [
        {"component": component, "key": key, "label": label, "share": share, "hint": hint}
        for key, label, share, hint in rows
    ]


# share = fraction of the component's ratio this signal contributes (sums to 1 per component).
SIGNAL_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "material": _share_rows("material", [
        ("reading", "研读覆盖", 0.50, "打开并读完每份必读文档（滚动到底、停留满 3 分钟或累计 5 分钟即算读完）"),
        ("mastery", "掌握检验", 0.35, "读完后通过文档末尾的掌握小测；没有小测的文档读完即视为掌握"),
        ("depth", "研读深度", 0.15, "每份文档的有效停留时长（满 3 分钟计满）"),
    ]),
    "task": _share_rows("task", [
        ("completion", "任务完成", 0.50, "提交每一次作业、测验与小组任务"),
        ("quality", "完成质量", 0.30, "已批改任务的得分均值；已交未批按 68% 暂记"),
        ("punctuality", "按时提交", 0.12, "在截止时间前提交的比例"),
        ("breadth", "任务广度", 0.08, "作业、考试、小组任务等不同类型都有参与"),
    ]),
    "interaction": _share_rows("interaction", [
        ("ai_questions", "AI 提问", 0.26, "向课堂 AI 助教提出有效问题（满 8 次计满）"),
        ("discussion", "研讨发言", 0.18, "在课堂研讨室发言（满 12 条计满）"),
        ("mentions", "@助教追问", 0.12, "在研讨室 @助教 / @AI 追问（满 4 次计满）"),
        ("private", "私信求助", 0.12, "私信教师或助教（满 3 次计满）"),
        ("peer_help", "同伴互助", 0.14, "回复被同学或教师标记为有帮助；高境界修士互助加倍计分"),
        ("live", "课堂活动", 0.18, "参与课堂投票、即时互动、分组讨论（满 6 次计满）"),
    ]),
    "consistency": _share_rows("consistency", [
        ("active_days", "活跃天数", 0.30, "有学习行为的天数（满 5 天计满）"),
        ("streak", "连续修炼", 0.20, "最长连续活跃天数（满 4 天计满）"),
        ("focus", "专注时长", 0.22, "课堂页面处于前台且有操作的时长（满 90 分钟计满）"),
        ("online", "在线时长", 0.18, "课堂在线累计时长（满 2 小时计满）"),
        ("activity", "操作频次", 0.10, "课堂内的有效操作次数（满 80 次计满）"),
    ]),
}

SIGNAL_CAPS = {
    "ai_questions": 8, "discussion": 12, "mentions": 4, "private": 3, "peer_help": 4, "live": 6,
    "active_days": 5, "streak": 4, "focus": 5400, "online": 7200, "activity": 80,
}

COMPONENT_DESCRIPTIONS = {
    "material": "读完并真正掌握每一份必读学习文档",
    "task": "按时、高质量地完成每一次作业与考试",
    "interaction": "提问、讨论、互助与课堂活动参与",
    "consistency": "每天都来、专注投入的稳定节奏",
}


def signal_share(component: str, key: str) -> float:
    for item in SIGNAL_DEFINITIONS.get(component, []):
        if item["key"] == key:
            return float(item["share"])
    return 0.0


def _signal_ratios_from_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, tuple[float, str]]]:
    """Extract (ratio, value_label) per signal; tolerant of partial/legacy metrics."""
    material = metrics.get("material") if isinstance(metrics.get("material"), dict) else {}
    assignments = metrics.get("assignments") if isinstance(metrics.get("assignments"), dict) else {}
    interactions = metrics.get("interactions") if isinstance(metrics.get("interactions"), dict) else {}

    required = int(_num(material.get("required_count")))
    completed = int(_num(material.get("completed_count")))
    mastered = int(_num(material.get("mastered_count")))
    material_ratio = _clamp(material.get("ratio"))
    reading_ratio = _clamp(_num(material.get("reading_ratio"), completed / required if required else material_ratio))
    mastery_ratio = _clamp(_num(material.get("mastery_ratio"), mastered / required if required else material_ratio))
    depth_ratio = _clamp(_num(material.get("depth_ratio"), material_ratio))

    assignment_count = int(_num(assignments.get("assignment_count")))
    submitted = int(_num(assignments.get("submitted_count")))
    graded = int(_num(assignments.get("graded_count")))
    completion = _clamp(assignments.get("completion_ratio"))
    quality = _clamp(assignments.get("score_ratio"))
    punctuality = _clamp(_num(assignments.get("punctuality_ratio"), completion))
    breadth = _clamp(_num(assignments.get("breadth_ratio"), 1.0 if submitted else 0.0))
    on_time = int(_num(assignments.get("on_time_count"), submitted))

    ai_q = int(_num(interactions.get("ai_question_count")))
    msgs = int(_num(interactions.get("chat_message_count")))
    mentions = int(_num(interactions.get("assistant_mention_count")))
    private = int(_num(interactions.get("private_teacher_count")))
    peer_units = _num(interactions.get("peer_help_units"))
    peer_count = int(_num(interactions.get("peer_help_count")))
    live_units = int(_num(interactions.get("live_participation_count")))
    active_days = int(_num(interactions.get("active_days")))
    streak = int(_num(interactions.get("streak_days"), min(active_days, 1)))
    focus_seconds = int(_num(interactions.get("focus_seconds")))
    online_seconds = int(_num(interactions.get("online_seconds")))
    activity_count = int(_num(interactions.get("activity_count")))
    caps = SIGNAL_CAPS

    return {
        "material": {
            "reading": (reading_ratio, f"{completed} / {required} 份读完" if required else "暂无必读文档"),
            "mastery": (mastery_ratio, f"{mastered} / {required} 份掌握" if required else "暂无必读文档"),
            "depth": (depth_ratio, f"深度 {int(round(depth_ratio * 100))}%"),
        },
        "task": {
            "completion": (completion, f"{submitted} / {assignment_count} 已提交" if assignment_count else "暂无任务"),
            "quality": (quality, f"均分 {int(round(quality * 100))}" if graded else ("待批改" if submitted else "暂无成绩")),
            "punctuality": (punctuality, f"{on_time} / {submitted} 按时" if submitted else "暂无提交"),
            "breadth": (breadth, f"覆盖 {int(round(breadth * 100))}% 任务类型" if assignment_count else "暂无任务"),
        },
        "interaction": {
            "ai_questions": (_clamp(ai_q / caps["ai_questions"]), f"{ai_q} 次"),
            "discussion": (_clamp(msgs / caps["discussion"]), f"{msgs} 条"),
            "mentions": (_clamp(mentions / caps["mentions"]), f"{mentions} 次"),
            "private": (_clamp(private / caps["private"]), f"{private} 次"),
            "peer_help": (_clamp(peer_units / caps["peer_help"]), f"{peer_count} 次"),
            "live": (_clamp(live_units / caps["live"]), f"{live_units} 次"),
        },
        "consistency": {
            "active_days": (_clamp(active_days / caps["active_days"]), f"{active_days} 天"),
            "streak": (_clamp(streak / caps["streak"]), f"连续 {streak} 天"),
            "focus": (_clamp(focus_seconds / caps["focus"]), f"{focus_seconds // 60} 分钟"),
            "online": (_clamp(online_seconds / caps["online"]), f"{online_seconds // 60} 分钟"),
            "activity": (_clamp(activity_count / caps["activity"]), f"{activity_count} 次"),
        },
    }


def build_component_breakdown(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Explain each component's points with its signals.

    Signal points are scaled so they sum to the component's recorded points, which
    keeps the breakdown consistent with the stored components even when a metric
    loader is older than this model (missing keys fall back to coarse values).
    """
    weights = metrics.get("weights") if isinstance(metrics.get("weights"), dict) else {}
    components = metrics.get("components") if isinstance(metrics.get("components"), dict) else {}
    ratios = _signal_ratios_from_metrics(metrics)
    rows: list[dict[str, Any]] = []
    for key in CULTIVATION_WEIGHT_KEYS:
        weight = _num(weights.get(key), DEFAULT_CULTIVATION_WEIGHTS[key])
        points = round(_num(components.get(key)), 1)
        definitions = SIGNAL_DEFINITIONS[key]
        signal_ratios = ratios.get(key, {})
        weighted = [(item, _clamp(signal_ratios.get(item["key"], (0.0, ""))[0]) * item["share"]) for item in definitions]
        weighted_total = sum(value for _, value in weighted)
        signals = []
        for item, value in weighted:
            ratio, value_label = signal_ratios.get(item["key"], (0.0, ""))
            share_points = (points * value / weighted_total) if weighted_total > 0 else 0.0
            signals.append({
                "key": item["key"],
                "label": item["label"],
                "hint": item["hint"],
                "share": item["share"],
                "ratio": round(_clamp(ratio), 4),
                "percent": int(round(_clamp(ratio) * 100)),
                "points": round(share_points, 1),
                "max_points": round(weight * item["share"], 1),
                "value_label": value_label,
            })
        ratio_total = (points / weight) if weight > 0 else 0.0
        rows.append({
            "key": key,
            "label": CULTIVATION_WEIGHT_LABELS[key],
            "description": COMPONENT_DESCRIPTIONS[key],
            "weight": int(round(weight)),
            "points": points,
            "ratio": round(_clamp(ratio_total), 4),
            "percent": int(round(_clamp(ratio_total) * 100)),
            "remaining": round(max(0.0, weight - points), 1),
            "signals": signals,
        })
    return rows


def build_growth_summary(
    raw_score: float,
    score: float,
    *,
    next_stage: dict[str, Any] | None,
    exponent: float | None = None,
) -> dict[str, Any]:
    k = exponent if exponent is not None else growth_curve_exponent()
    raw = round(_clamp(_num(raw_score) / 100) * 100, 1)
    shown = round(_clamp(_num(score) / 100) * 100, 1)
    # Marginal value of the next raw point, relative to a linear scale.
    marginal = round(k * (1 - raw / 100) ** (k - 1), 2) if raw < 100 else 0.0
    if raw < 25:
        phase_key, phase_label = "early", "起步期 · 每一分努力都快速转化为修为"
    elif raw < 60:
        phase_key, phase_label = "middle", "进阶期 · 修为稳步增长，开始需要更扎实的积累"
    elif raw < 90:
        phase_key, phase_label = "late", "深耕期 · 同样的努力带来的修为增长放缓"
    else:
        phase_key, phase_label = "summit", "巅峰期 · 最后几分最难，也最见真功夫"
    summary: dict[str, Any] = {
        "model_version": GROWTH_MODEL_VERSION,
        "exponent": round(k, 2),
        "raw_score": raw,
        "score": shown,
        "bonus": round(max(0.0, shown - raw), 1),
        "marginal_value": marginal,
        "phase": phase_key,
        "phase_label": phase_label,
        "note": "修为按成长曲线换算：起步阶段每一分基础积累都放大计入，越接近巅峰同样的努力换来的修为越少；但每一境都必须通过破境试炼才能晋升。",
    }
    if next_stage:
        unlock = _num(next_stage.get("unlock_score"))
        raw_required = raw_score_for(unlock, exponent=k)
        summary["next_stage"] = {
            "key": str(next_stage.get("key") or ""),
            "unlock_score": unlock,
            "raw_required": raw_required,
            "raw_gap": round(max(0.0, raw_required - raw), 1),
            "score_gap": round(max(0.0, unlock - shown), 1),
        }
    return summary


def longest_streak(dates: Iterable[Any]) -> int:
    """Longest run of consecutive ISO dates (YYYY-MM-DD prefixes)."""
    parsed: set[date] = set()
    for raw in dates:
        text = str(raw or "")[:10]
        try:
            parsed.add(date.fromisoformat(text))
        except ValueError:
            continue
    if not parsed:
        return 0
    best = 0
    for day in parsed:
        if date.fromordinal(day.toordinal() - 1) in parsed:
            continue
        length = 1
        while date.fromordinal(day.toordinal() + length) in parsed:
            length += 1
        best = max(best, length)
    return best
