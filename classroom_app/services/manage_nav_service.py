from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


MANAGE_DOMAIN_ORDER = ("teaching", "academic", "teacher")
MANAGE_ADMIN_DOMAIN = "admin"

MANAGE_DOMAIN_META: dict[str, dict[str, str]] = {
    "teaching": {
        "label": "教学",
        "short_label": "教学",
        "title": "教学域",
        "description": "管理我开的课、课堂内容、开课流程与教学对象。",
        "accent": "#4f46e5",
    },
    "academic": {
        "label": "教务",
        "short_label": "教务",
        "title": "教务域",
        "description": "整合课表、考试、监考、教室、公文和学校事务数据。",
        "accent": "#0f766e",
    },
    "teacher": {
        "label": "教师",
        "short_label": "教师",
        "title": "教师域",
        "description": "照看我自己的资料、安全、通知、签名和对接凭据。",
        "accent": "#b45309",
    },
    MANAGE_ADMIN_DOMAIN: {
        "label": "平台管理",
        "short_label": "管理",
        "title": "平台管理",
        "description": "超管教师维护用户、组织、预算、诊断与平台工具。",
        "accent": "#d97706",
    },
}


@dataclass(frozen=True)
class ManageNavItem:
    key: str
    domain: str
    group: str
    label: str
    icon: str
    href: str
    search_text: str
    ai_hint: str
    required_flag: str = ""
    legacy_hrefs: tuple[str, ...] = ()


MANAGE_NAV_ITEMS: tuple[ManageNavItem, ...] = (
    ManageNavItem(
        key="workflow",
        domain="teaching",
        group="域首页",
        label="教学工作台",
        icon="workflow",
        href="/manage/teaching",
        search_text="教学 工作台 流程 开课向导 workflow",
        ai_hint="教学工作台：按开课流程检查学期、课程、班级、教材、材料和 AI 助教配置。",
        legacy_hrefs=("/manage",),
    ),
    ManageNavItem(
        key="semesters",
        domain="teaching",
        group="开课准备",
        label="确认学期",
        icon="calendar",
        href="/manage/teaching/semesters",
        search_text="确认学期 学期 校历 semester calendar",
        ai_hint="确认学期：维护学期区间、周次规则与校历，供开课和课堂排期使用。",
        legacy_hrefs=("/manage/semesters",),
    ),
    ManageNavItem(
        key="offerings",
        domain="teaching",
        group="开课准备",
        label="开设课堂",
        icon="plus",
        href="/manage/teaching/offerings",
        search_text="开设课堂 课堂 offering 班级 课程 教材",
        ai_hint="开设课堂：把学期、班级、课程、教材和排课信息组合成可进入的课堂。",
        legacy_hrefs=("/manage/offerings",),
    ),
    ManageNavItem(
        key="ai",
        domain="teaching",
        group="开课准备",
        label="配置 AI 助教",
        icon="bot",
        href="/manage/teaching/ai",
        search_text="配置 AI 助教 人工智能 ai prompt",
        ai_hint="配置 AI 助教：为具体课堂维护提示词、教材与知识依据。",
        legacy_hrefs=("/manage/ai",),
    ),
    ManageNavItem(
        key="classes",
        domain="teaching",
        group="教学对象",
        label="班级",
        icon="users",
        href="/manage/teaching/classes",
        search_text="班级 学生 名册 class roster",
        ai_hint="班级：维护教学对象、学生名单、组织归属和课堂可用班级。",
        legacy_hrefs=("/manage/classes",),
    ),
    ManageNavItem(
        key="courses",
        domain="teaching",
        group="内容资产",
        label="课程",
        icon="book-open",
        href="/manage/teaching/courses",
        search_text="课程 模板 课次 course lesson",
        ai_hint="课程：维护课程模板、简介、学时学分和课次结构。",
        legacy_hrefs=("/manage/courses",),
    ),
    ManageNavItem(
        key="textbooks",
        domain="teaching",
        group="内容资产",
        label="教材",
        icon="book",
        href="/manage/teaching/textbooks",
        search_text="教材 textbook 参考书",
        ai_hint="教材：维护课程教材与附件，供开课和 AI 助教引用。",
        legacy_hrefs=("/manage/textbooks",),
    ),
    ManageNavItem(
        key="exams",
        domain="teaching",
        group="内容资产",
        label="试卷",
        icon="file-text",
        href="/manage/teaching/exams",
        search_text="试卷 题库 考试 exam paper",
        ai_hint="试卷：管理教师试卷库、题目、考试配置与分配入口。",
        legacy_hrefs=("/manage/exams",),
    ),
    ManageNavItem(
        key="materials",
        domain="teaching",
        group="内容资产",
        label="材料",
        icon="folder",
        href="/manage/teaching/materials",
        search_text="材料 资料 文件 course material",
        ai_hint="材料：整理课程文档、文件夹和可分发给课堂的学习资料。",
        legacy_hrefs=("/manage/materials",),
    ),
    ManageNavItem(
        key="system_smart_classroom_integrations",
        domain="teaching",
        group="课堂工具",
        label="智慧课堂",
        icon="bar-chart",
        href="/manage/teaching/smart-classroom-integrations",
        search_text="智慧课堂 点名 签到 smart classroom attendance",
        ai_hint="智慧课堂：配置智慧课堂点名、签到和课堂考勤同步能力。",
        legacy_hrefs=("/manage/system/smart-classroom-integrations",),
    ),
    ManageNavItem(
        key="academic_overview",
        domain="academic",
        group="域首页",
        label="教务总览",
        icon="gauge",
        href="/manage/academic",
        search_text="教务 总览 课表 考试 监考 academic overview",
        ai_hint="教务总览：查看教务同步、监考考试提醒、教室和公文的聚合入口。",
    ),
    ManageNavItem(
        key="system_academic_integrations",
        domain="academic",
        group="数据同步",
        label="教务对接",
        icon="id-card",
        href="/manage/academic/integrations",
        search_text="教务对接 教务系统 课表 考务 名册 academic",
        ai_hint="教务对接：同步课表、考务、监考和名册等学校教务数据。",
        legacy_hrefs=("/manage/system/academic-integrations",),
    ),
    ManageNavItem(
        key="classrooms",
        domain="academic",
        group="场地",
        label="教室与空闲教室",
        icon="building",
        href="/manage/academic/classrooms",
        search_text="教室 教学场地 空闲教室 classroom room",
        ai_hint="教室与空闲教室：查询教学场地、同步教室数据并筛选空闲教室。",
        legacy_hrefs=("/manage/classrooms",),
    ),
    ManageNavItem(
        key="gongwen",
        domain="academic",
        group="公文",
        label="公文列表",
        icon="file-text",
        href="/manage/academic/gongwen",
        search_text="公文 通知 文件 红头文件 gongwen document",
        ai_hint="公文列表：检索学校和学院公文、查看正文与附件、处理关注命中。",
        legacy_hrefs=("/manage/gongwen",),
    ),
    ManageNavItem(
        key="system_gongwen_integrations",
        domain="academic",
        group="公文",
        label="公文同步",
        icon="refresh",
        href="/manage/academic/gongwen-sync",
        search_text="公文同步 校园公文通 统一认证 gongwen sync",
        ai_hint="公文同步：配置校园公文通并触发公文同步。",
        legacy_hrefs=("/manage/system/gongwen-integrations",),
    ),
    ManageNavItem(
        key="teacher_profile",
        domain="teacher",
        group="我的资料",
        label="我的概览",
        icon="user",
        href="/manage/me",
        search_text="我的概览 个人中心 资料 profile me",
        ai_hint="我的概览：查看教师个人资料完整度、通知、私信和常用个人入口。",
    ),
    ManageNavItem(
        key="signatures",
        domain="teacher",
        group="我的资料",
        label="我的签名",
        icon="pen",
        href="/manage/me/signatures",
        search_text="签名 电子签名 signature",
        ai_hint="我的签名：维护教师个人电子签名，供导出、审批和签章场景使用。",
        legacy_hrefs=("/manage/signatures",),
    ),
    ManageNavItem(
        key="teacher_credentials",
        domain="teacher",
        group="账号与安全",
        label="我的对接凭据",
        icon="link",
        href="/manage/me/credentials",
        search_text="对接凭据 教务 智慧课堂 公文通 账号 credential",
        ai_hint="我的对接凭据：集中查看教师个人教务、智慧课堂和公文通账号凭据状态。",
    ),
    ManageNavItem(
        key="system_password_resets",
        domain="teacher",
        group="账号与安全",
        label="学生找回申请",
        icon="lock",
        href="/manage/me/password-resets",
        search_text="找回申请 密码 学生 password reset",
        ai_hint="学生找回申请：教师审核和处理自己班级学生的账号找回事务。",
        legacy_hrefs=("/manage/system/password-resets",),
    ),
    ManageNavItem(
        key="system_users",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="用户管理",
        icon="users",
        href="/manage/system/users",
        search_text="用户管理 教师账号 user admin",
        ai_hint="用户管理：超管教师维护教师账号和平台用户状态。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_organizations",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="学校组织",
        icon="building",
        href="/manage/system/organizations",
        search_text="学校组织 学院 系部 organization",
        ai_hint="学校组织：超管教师维护学校、学院和系部组织目录。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_feedback",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="问题反馈",
        icon="file-text",
        href="/manage/system/feedback",
        search_text="问题反馈 feedback",
        ai_hint="问题反馈：超管教师查看和处理全站用户反馈。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_blog_crawler",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="AI博客管家",
        icon="bot",
        href="/manage/system/blog-crawler",
        search_text="AI博客管家 爬虫 新闻 blog crawler",
        ai_hint="AI博客管家：超管教师维护新闻爬取、摘要和博客发布队列。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_ai_usage",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="AI 用量",
        icon="line-chart",
        href="/manage/system/ai-usage",
        search_text="AI 用量 预算 成本 usage budget",
        ai_hint="AI 用量：超管教师查看 AI 预算、成本和使用趋势。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_agent_keys",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="Agent Key",
        icon="key",
        href="/manage/system/agent-keys",
        search_text="Agent Key 密钥 token",
        ai_hint="Agent Key：超管教师维护 Agent 运行时密钥。",
        required_flag="super_admin",
    ),
    ManageNavItem(
        key="system_diagnostics",
        domain=MANAGE_ADMIN_DOMAIN,
        group="平台管理",
        label="压测诊断",
        icon="activity",
        href="/manage/system/diagnostics",
        search_text="压测诊断 性能 diagnostics",
        ai_hint="压测诊断：超管教师查看运行健康、压测入口和后台任务状态。",
        required_flag="super_admin",
    ),
)


_NAV_ITEMS_BY_KEY = {item.key: item for item in MANAGE_NAV_ITEMS}
_LEGACY_KEY_ALIASES: dict[str, str] = {
    "system_super_admin": "system_users",
}


def normalize_manage_nav_key(active_key: Any) -> str:
    key = str(active_key or "").strip()
    return _LEGACY_KEY_ALIASES.get(key, key)


def get_manage_nav_item(key: Any) -> ManageNavItem | None:
    return _NAV_ITEMS_BY_KEY.get(normalize_manage_nav_key(key))


def canonical_manage_href(key: str, fallback: str = "/manage/teaching") -> str:
    item = get_manage_nav_item(key)
    return item.href if item else fallback


def _can_view_item(item: ManageNavItem, *, is_super_admin: bool) -> bool:
    if item.required_flag == "super_admin":
        return is_super_admin
    return True


def _item_to_template_dict(item: ManageNavItem, *, active_key: str) -> dict[str, Any]:
    meta = MANAGE_DOMAIN_META[item.domain]
    return {
        "key": item.key,
        "domain": item.domain,
        "domain_label": meta["label"],
        "group": item.group,
        "label": item.label,
        "icon": item.icon,
        "href": item.href,
        "search_text": item.search_text,
        "ai_hint": item.ai_hint,
        "required_flag": item.required_flag,
        "legacy_hrefs": list(item.legacy_hrefs),
        "active": item.key == active_key,
    }


def _group_template_items(items: Iterable[ManageNavItem], *, active_key: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_group.setdefault(item.group, []).append(_item_to_template_dict(item, active_key=active_key))
    for group, group_items in by_group.items():
        groups.append({
            "label": group,
            "items": group_items,
            "active": any(item["active"] for item in group_items),
        })
    return groups


def build_manage_nav(
    user: dict[str, Any] | None,
    active_key: str,
    *,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    del user
    normalized_active_key = normalize_manage_nav_key(active_key)
    active_item = get_manage_nav_item(normalized_active_key)
    active_domain = active_item.domain if active_item and active_item.domain != MANAGE_ADMIN_DOMAIN else "teaching"
    if active_item and active_item.domain == MANAGE_ADMIN_DOMAIN:
        active_domain = MANAGE_ADMIN_DOMAIN

    visible_items = [
        item
        for item in MANAGE_NAV_ITEMS
        if _can_view_item(item, is_super_admin=is_super_admin)
    ]
    hrefs = {item.key: item.href for item in visible_items}

    domains = []
    for domain_key in MANAGE_DOMAIN_ORDER:
        domain_items = [item for item in visible_items if item.domain == domain_key]
        meta = MANAGE_DOMAIN_META[domain_key]
        first_href = domain_items[0].href if domain_items else "#"
        groups = _group_template_items(domain_items, active_key=normalized_active_key)
        domains.append({
            "key": domain_key,
            **meta,
            "href": first_href,
            "active": domain_key == active_domain,
            "groups": groups,
        })

    admin_items = [item for item in visible_items if item.domain == MANAGE_ADMIN_DOMAIN]
    admin_groups = _group_template_items(admin_items, active_key=normalized_active_key)

    return {
        "active_key": normalized_active_key,
        "active_domain": active_domain,
        "active_item": _item_to_template_dict(active_item, active_key=normalized_active_key) if active_item else None,
        "domains": domains,
        "admin_groups": admin_groups,
        "hrefs": hrefs,
        "domain_meta": MANAGE_DOMAIN_META,
    }


def iter_manage_legacy_redirects() -> list[dict[str, str]]:
    redirects: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in MANAGE_NAV_ITEMS:
        for legacy_href in item.legacy_hrefs:
            if not legacy_href or legacy_href == item.href or legacy_href in seen:
                continue
            redirects.append({
                "key": item.key,
                "legacy_href": legacy_href,
                "canonical_href": item.href,
            })
            seen.add(legacy_href)
    return redirects


def iter_platform_manage_routes(*, include_admin: bool = False) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for item in MANAGE_NAV_ITEMS:
        if item.domain == MANAGE_ADMIN_DOMAIN and not include_admin:
            continue
        domain_label = MANAGE_DOMAIN_META[item.domain]["label"]
        routes.append({
            "path": item.href,
            "label": f"教师管理中心 · {domain_label} · {item.group} · {item.label}（{item.ai_hint}）",
            "roles": "teacher",
        })
    return routes


def build_dashboard_domain_cards() -> list[dict[str, Any]]:
    card_items = {
        "teaching": ("offerings", "materials", "ai"),
        "academic": ("academic_overview", "classrooms", "gongwen"),
        "teacher": ("teacher_profile", "teacher_credentials", "system_password_resets"),
    }
    cards: list[dict[str, Any]] = []
    for domain_key in MANAGE_DOMAIN_ORDER:
        meta = MANAGE_DOMAIN_META[domain_key]
        item_keys = card_items[domain_key]
        actions = []
        for item_key in item_keys:
            item = get_manage_nav_item(item_key)
            if item:
                actions.append({
                    "label": item.label,
                    "href": item.href,
                    "hint": item.ai_hint,
                })
        cards.append({
            "domain": domain_key,
            "label": meta["label"],
            "title": meta["title"],
            "description": meta["description"],
            "href": actions[0]["href"] if actions else "/manage/teaching",
            "actions": actions,
        })
    return cards
