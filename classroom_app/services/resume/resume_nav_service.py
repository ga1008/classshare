"""Left-rail navigation registry for the student resume console.

Mirrors the teacher manage-center nav idea (``manage_nav_service``) but far
simpler: a flat ordered list of items grouped into two categories — 个人资料 and
简历管理. The page handler passes ``active_key`` and gets back a template-ready
structure consumed by ``templates/resume/layout.html``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResumeNavItem:
    key: str
    group: str
    label: str
    icon: str
    href: str
    search_text: str


RESUME_NAV_ITEMS: tuple[ResumeNavItem, ...] = (
    ResumeNavItem(
        key="home",
        group="求职工作台",
        label="开始求职",
        icon="home",
        href="/resume",
        search_text="求职工作台 开始 导入简历 推荐岗位 career resume home",
    ),
    ResumeNavItem(
        key="job_targets",
        group="求职工作台",
        label="岗位分析",
        icon="target",
        href="/resume/job-targets",
        search_text="岗位分析 职位描述 JD 要求 缺口 定向简历 job target analysis",
    ),
    ResumeNavItem(
        key="applications",
        group="求职工作台",
        label="投递进展",
        icon="clipboard",
        href="/resume/applications",
        search_text="投递进展 公司 岗位 笔试 面试 offer application tracker",
    ),
    ResumeNavItem(
        key="personal",
        group="个人资料",
        label="个人信息",
        icon="user",
        href="/resume/profile/personal",
        search_text="个人信息 姓名 联系方式 期望岗位 personal info",
    ),
    ResumeNavItem(
        key="education",
        group="个人资料",
        label="学历",
        icon="graduation",
        href="/resume/profile/education",
        search_text="学历 学习经历 高中 大学 培训 education",
    ),
    ResumeNavItem(
        key="experience",
        group="个人资料",
        label="经验",
        icon="briefcase",
        href="/resume/profile/experience",
        search_text="经验 项目 比赛 experience project competition",
    ),
    ResumeNavItem(
        key="skill",
        group="个人资料",
        label="技能",
        icon="sparkles",
        href="/resume/profile/skill",
        search_text="技能 能力 skill",
    ),
    ResumeNavItem(
        key="certificate",
        group="个人资料",
        label="证书",
        icon="award",
        href="/resume/profile/certificate",
        search_text="证书 资格证 certificate",
    ),
    ResumeNavItem(
        key="self_intro",
        group="个人资料",
        label="自我介绍",
        icon="quote",
        href="/resume/profile/self-intro",
        search_text="自我介绍 个人介绍 self introduction",
    ),
    ResumeNavItem(
        key="builder",
        group="简历管理",
        label="新建简历",
        icon="layout",
        href="/resume/builder",
        search_text="新建简历 搭建 制作 builder create resume",
    ),
    ResumeNavItem(
        key="list",
        group="简历管理",
        label="我的简历",
        icon="files",
        href="/resume/list",
        search_text="我的简历 简历列表 导出 word pdf resumes",
    ),
)

RESUME_GROUP_ORDER = ("求职工作台", "个人资料", "简历管理")

_ITEMS_BY_KEY = {item.key: item for item in RESUME_NAV_ITEMS}


def get_resume_nav_item(key: str) -> ResumeNavItem | None:
    return _ITEMS_BY_KEY.get(str(key or "").strip())


def build_resume_nav(active_key: str) -> dict[str, Any]:
    """Return a template-ready nav structure with the active item flagged."""
    active = str(active_key or "").strip()
    groups: list[dict[str, Any]] = []
    for group_label in RESUME_GROUP_ORDER:
        items = [
            {
                "key": item.key,
                "label": item.label,
                "icon": item.icon,
                "href": item.href,
                "search_text": item.search_text,
                "active": item.key == active,
            }
            for item in RESUME_NAV_ITEMS
            if item.group == group_label
        ]
        groups.append({
            "label": group_label,
            "items": items,
            "active": any(i["active"] for i in items),
        })
    return {"active_key": active, "groups": groups}
