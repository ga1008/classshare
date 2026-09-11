"""学期阶段条（docs/manage-center-improvement-plan-2026-09-11.md §5.5）。

课堂管理页顶部的一行：判断当前处于学期初 / 学期中 / 学期末，学期初默认展开
「开课清单」（学期 / 课堂 / 教材 / AI 助教 / 排课 / 一键开课候选），学期中折叠成一行，
学期末指向成绩与归档。纯函数，不查库——全部输入来自 offering_hub_service 已经算好的数据。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 阈值集中定义：学期开始后 2 周内 = 学期初（开始前也算）；结束前 3 周起 = 学期末。
STAGE_START_GRACE_DAYS = 14
STAGE_END_LEAD_DAYS = 21

PHASE_LABELS = {
    "start": "学期初",
    "middle": "学期中",
    "end": "学期末",
    "none": "未确认学期",
}


def _parse(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_teaching_phase(start: date | None, end: date | None, today: date) -> str:
    if not start or not end:
        return "none"
    if today <= start + timedelta(days=STAGE_START_GRACE_DAYS):
        return "start"
    if today >= end - timedelta(days=STAGE_END_LEAD_DAYS):
        return "end"
    return "middle"


def _week_index(start: date | None, today: date) -> int:
    if not start or today < start:
        return 0
    return (today - start).days // 7 + 1


def build_teaching_stage(
    *,
    semesters: list[dict[str, Any]],
    default_semester_id: int | None,
    hub_stats: dict[str, Any],
    hub_todo: dict[str, Any],
    hub_bootstrap: dict[str, Any] | None,
    today: date,
) -> dict[str, Any]:
    semester = next(
        (s for s in semesters if default_semester_id is not None and s.get("id") is not None and int(s["id"]) == int(default_semester_id)),
        None,
    )
    start = _parse(semester.get("start_date")) if semester else None
    end = _parse(semester.get("end_date")) if semester else None
    phase = resolve_teaching_phase(start, end, today)
    week = _week_index(start, today)
    week_count = int(semester.get("week_count") or 0) if semester else 0

    offering_count = int(hub_stats.get("current_offering_count") or 0)
    missing_textbook = int(hub_todo.get("missing_textbook") or 0)
    missing_ai = int(hub_todo.get("missing_ai") or 0)
    unscheduled = int(hub_todo.get("unscheduled") or 0)
    candidate_count = int(((hub_bootstrap or {}).get("summary") or {}).get("candidate_count") or 0)

    checklist = [
        {"key": "semester", "label": "确认学期", "done": semester is not None,
         "detail": (semester or {}).get("name") or "还没有本学期", "href": "/manage/teaching/semesters"},
        {"key": "offerings", "label": "开设课堂", "done": offering_count > 0,
         "detail": f"{offering_count} 个课堂" if offering_count else (f"{candidate_count} 个教学班可一键开课" if candidate_count else "还没有课堂"),
         "href": "/manage/teaching/offerings"},
        {"key": "textbook", "label": "绑定教材", "done": offering_count > 0 and missing_textbook == 0,
         "detail": f"{missing_textbook} 个课堂缺教材" if missing_textbook else ("全部就绪" if offering_count else "开课后绑定"),
         "href": "/manage/teaching/offerings"},
        {"key": "ai", "label": "配置 AI 助教", "done": offering_count > 0 and missing_ai == 0,
         "detail": f"{missing_ai} 个课堂未配置" if missing_ai else ("全部就绪" if offering_count else "开课后配置"),
         "href": "/manage/teaching/ai"},
        {"key": "schedule", "label": "排课", "done": offering_count > 0 and unscheduled == 0,
         "detail": f"{unscheduled} 个课堂未排课" if unscheduled else ("全部就绪" if offering_count else "开课后排课"),
         "href": "/manage/teaching/offerings"},
    ]
    done_count = sum(1 for item in checklist if item["done"])
    open_count = len(checklist) - done_count

    if phase == "none":
        headline = "先确认本学期，课堂、排课与成绩链都挂在学期上"
        next_href, next_label = "/manage/teaching/semesters", "确认学期"
    elif phase == "start":
        headline = f"开课清单完成 {done_count}/{len(checklist)}" + (f" · {candidate_count} 个教学班可一键开课" if candidate_count else "")
        if candidate_count or offering_count == 0:
            next_href, next_label = "/manage/teaching/offerings", "一键开设课堂"
        elif open_count:
            next_href, next_label = "/manage/teaching/ai", "补齐配置"
        else:
            next_href, next_label = "", ""
    elif phase == "end":
        headline = f"{offering_count} 个课堂进入收尾 · 去成绩与归档"
        next_href, next_label = "/manage/archive/ordinary-grade-records", "去成绩与归档"
    else:
        headline = f"{offering_count} 个课堂进行中" + (f" · {open_count} 项配置缺口" if open_count and offering_count else "")
        next_href, next_label = ("/manage/teaching/offerings", "补齐配置") if (open_count and offering_count) else ("", "")

    return {
        "phase": phase,
        "phase_label": PHASE_LABELS[phase],
        "semester_name": (semester or {}).get("name") or "",
        "week": week,
        "week_count": week_count,
        "week_label": (f"第 {week} 周" + (f" / {week_count} 周" if week_count else "")) if week else "",
        "headline": headline,
        "checklist": checklist,
        "done_count": done_count,
        "expanded": phase in {"start", "none"},
        "next_href": next_href,
        "next_label": next_label,
        "candidate_count": candidate_count,
    }
