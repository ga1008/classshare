"""统一收件箱（work inbox）。

docs/manage-center-improvement-plan-2026-09-11.md §5.3：教师所有「等我处理」的事项来自同一份数据。
本模块不新建表、不写 SQL——它是 ``dashboard_workspace_service`` 的薄门面：
workspace 的来源生成器已经聚合了日程 / 待批改 / 找回申请 / 审批申请 / 签名申请 /
课堂配置缺口 / 用户反馈（超管），这里只负责：

* 来源注册表（标签 / 语义色 / 全量页链接），供首页、待我处理页、域卡与 AI 平台知识复用；
* 按来源计数；
* 首页「需要处理」与 ``/manage/me/inbox`` 用同一个函数取数。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkInboxSource:
    key: str
    label: str
    tone: str
    domain: str
    href: str
    description: str


# 顺序即「待我处理」页的 chip 顺序，也是同紧急度内的排序依据。
WORK_INBOX_SOURCES: tuple[WorkInboxSource, ...] = (
    WorkInboxSource("approval", "审批申请", "warning", "me", "/manage/me/inbox?source=approval", "学生提交的作业撤回重做等申请，等我批准或拒绝。"),
    WorkInboxSource("signature_request", "签名申请", "warning", "me", "/manage/me/signature-workflows", "同事申请使用我的签名。"),
    WorkInboxSource("password_reset", "找回申请", "warning", "me", "/manage/me/password-resets", "我班学生的账号找回申请。"),
    WorkInboxSource("grading", "待批改", "primary", "teaching", "/manage/me/inbox?source=grading", "已提交、等我批改的答卷。"),
    WorkInboxSource("offering_gap", "课堂配置", "info", "teaching", "/manage/teaching/classroom-hub", "本学期课堂缺教材、未配 AI 助教或未排课。"),
    WorkInboxSource("teacher_calendar", "教务日程", "info", "academic", "/manage/academic", "监考、考试等教务安排。"),
    WorkInboxSource("manual", "个人待办", "slate", "home", "/dashboard", "我自己添加的待办。"),
    WorkInboxSource("feedback", "用户反馈", "rose", "admin", "/manage/system/feedback", "平台用户提交的问题反馈（超管）。"),
)
_SOURCE_BY_KEY = {source.key: source for source in WORK_INBOX_SOURCES}
_SOURCE_ORDER = {source.key: index for index, source in enumerate(WORK_INBOX_SOURCES)}


def get_work_inbox_source(key: str) -> WorkInboxSource | None:
    return _SOURCE_BY_KEY.get(str(key or ""))


def _is_open(item: dict[str, Any]) -> bool:
    return bool(item.get("is_actionable")) and not bool(item.get("is_completed"))


def summarize_work_inbox(items: list[dict[str, Any]]) -> dict[str, Any]:
    """按来源计数；只数还需要处理的条目。"""
    counter: Counter[str] = Counter()
    for item in items:
        if _is_open(item):
            counter[str(item.get("source_type") or item.get("kind") or "other")] += 1
    sources = []
    for source in WORK_INBOX_SOURCES:
        count = int(counter.get(source.key, 0))
        sources.append({
            "key": source.key,
            "label": source.label,
            "tone": source.tone,
            "domain": source.domain,
            "href": source.href,
            "description": source.description,
            "count": count,
        })
    other = sum(count for key, count in counter.items() if key not in _SOURCE_BY_KEY)
    return {
        "total": int(sum(counter.values())),
        "counts": {key: int(count) for key, count in counter.items()},
        "sources": sources,
        "other": int(other),
        "approval_total": int(counter.get("approval", 0) + counter.get("signature_request", 0) + counter.get("password_reset", 0)),
    }


def build_work_inbox(conn, user: dict[str, Any], *, limit: int = 8, source: str = "", workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    """首页与待我处理页共用的取数入口。

    ``workspace`` 已由首页构建时直接传入，避免同一请求内二次取数；
    独立页面/API 不传则自己加载，本函数再筛可处理项。
    """
    if str(user.get("role") or "") != "teacher":
        return {"items": [], "total": 0, "counts": {}, "sources": [], "other": 0, "approval_total": 0, "source": "", "shown": 0}
    if workspace is None:
        from .dashboard_workspace_service import load_dashboard_workspace
        workspace = load_dashboard_workspace(conn, user=user, limit=100)
    pool = list(workspace.get("all_items") or workspace.get("focus_items") or [])
    open_items = [item for item in pool if _is_open(item)]
    summary = summarize_work_inbox(open_items)
    wanted = str(source or "").strip()
    if wanted:
        open_items = [item for item in open_items if str(item.get("source_type") or "") == wanted]
    # 逾期/今天优先；同档内审批类（等人）前置，其余沿用 workspace 顺序（sort 稳定）。
    open_items.sort(key=lambda item: (0 if item.get("date_bucket") in {"overdue", "today"} else 1, _SOURCE_ORDER.get(str(item.get("source_type") or ""), 99)))
    shown_items = open_items[: max(1, int(limit))] if limit else open_items
    return {
        **summary,
        "source": wanted,
        "items": shown_items,
        "shown": len(shown_items),
        "generated_at": workspace.get("generated_at", ""),
    }
