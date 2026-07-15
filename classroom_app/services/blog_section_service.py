from __future__ import annotations

import json
import re
from typing import Any

from ..db.connection import get_configured_db_engine


DEFAULT_BLOG_SECTION_KEY = "general"
CAREER_BLOG_SECTION_KEY = "career"
SECTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


DEFAULT_BLOG_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "section_key": DEFAULT_BLOG_SECTION_KEY,
        "name": "杂谈与故事",
        "short_name": "杂谈",
        "description": "收纳小说、随笔、校园故事、阅读札记与成长片段，让不必被专业标签定义的表达也有自己的频道。",
        "icon": "✦",
        "accent_color": "#2563eb",
        "sort_order": 20,
        "is_career": False,
        "source_keywords": ["小说与叙事", "随笔与杂谈", "校园故事与成长", "阅读与创作"],
        "source_templates": [],
    },
    {
        "section_key": CAREER_BLOG_SECTION_KEY,
        "name": "毕业新征程",
        "short_name": "就业",
        "description": "面向毕业生的岗位、招聘会、基层项目与就业政策，优先覆盖南宁、广西和珠三角。",
        "icon": "→",
        "accent_color": "#e11d48",
        "sort_order": 10,
        "is_career": True,
        "source_keywords": [
            "广西高校毕业生招聘",
            "南宁应届毕业生招聘",
            "广西事业单位校园招聘",
            "广西国企校招",
            "粤港澳大湾区校园招聘",
            "广州 深圳 珠海 东莞 佛山 应届生招聘",
            "广西 三支一扶 西部计划 高校毕业生",
            "广西 广东 高校毕业生就业政策",
        ],
        "source_templates": [
            {
                "name": "国家大学生就业服务平台",
                "url": "https://www.bing.com/news/search?q={{keyword_q}}&format=RSS&setlang=zh-CN&cc=CN&freshness={{bing_freshness}}",
                "kind": "keyword_rss",
                "requires_keyword_match": False,
                "query_suffix": "(site:ncss.cn OR site:mohrss.gov.cn)",
            },
            {
                "name": "广西公共就业与人才服务",
                "url": "https://www.bing.com/news/search?q={{keyword_q}}&format=RSS&setlang=zh-CN&cc=CN&freshness={{bing_freshness}}",
                "kind": "keyword_rss",
                "requires_keyword_match": False,
                "query_suffix": "(site:gxrc.com OR site:rst.gxzf.gov.cn OR site:nanning.gov.cn OR site:chrm.mohrss.gov.cn)",
            },
            {
                "name": "珠三角公共就业服务",
                "url": "https://www.bing.com/news/search?q={{keyword_q}}&format=RSS&setlang=zh-CN&cc=CN&freshness={{bing_freshness}}",
                "kind": "keyword_rss",
                "requires_keyword_match": False,
                "query_suffix": "(site:hrss.gd.gov.cn OR site:job.gdedu.gov.cn OR site:ggfw.hrss.gd.gov.cn OR site:sz.gov.cn OR site:gz.gov.cn OR site:dg.gov.cn)",
            },
        ],
    },
    {
        "section_key": "technology",
        "name": "科技前沿",
        "short_name": "科技",
        "description": "关注改变生活与产业的科学发现、工程创新和新兴技术。",
        "icon": "⌁",
        "accent_color": "#0f766e",
        "sort_order": 30,
        "is_career": False,
        "source_keywords": ["科技创新", "新能源与先进制造", "航空航天", "生物科技"],
        "source_templates": [],
    },
    {
        "section_key": "humanities",
        "name": "人文视界",
        "short_name": "人文",
        "description": "从文学、历史、社会与文化中理解人和我们共同生活的世界。",
        "icon": "文",
        "accent_color": "#b45309",
        "sort_order": 60,
        "is_career": False,
        "source_keywords": ["文学与阅读", "历史文化", "社会观察", "语言与传播"],
        "source_templates": [],
    },
    {
        "section_key": "computer",
        "name": "计算机",
        "short_name": "计算机",
        "description": "软件开发、开源生态、网络安全与计算基础设施的新鲜实践。",
        "icon": "</>",
        "accent_color": "#4f46e5",
        "sort_order": 40,
        "is_career": False,
        "source_keywords": ["软件开发", "开源技术", "网络安全", "云计算"],
        "source_templates": [],
    },
    {
        "section_key": "ai",
        "name": "AI 新知",
        "short_name": "AI",
        "description": "追踪人工智能研究、产品、治理与真实应用，保持好奇也保持判断。",
        "icon": "AI",
        "accent_color": "#7c3aed",
        "sort_order": 50,
        "is_career": False,
        "source_keywords": ["人工智能", "大语言模型", "机器学习", "生成式AI"],
        "source_templates": [],
    },
)


# Upgrade only historical system identities.  Labels and ordering identify the
# old defaults; free-form descriptions and existing source configuration remain
# administrator-owned and are preserved.
LEGACY_DEFAULT_SECTION_IDENTITIES: dict[str, tuple[str, str, int]] = {
    "general": ("校园与成长", "综合", 10),
    "technology": ("科技前沿", "科技", 20),
    "humanities": ("人文视界", "人文", 30),
    "career": ("毕业新征程", "就业", 60),
}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def ensure_default_blog_sections(conn) -> None:
    # Reads are the hot path.  Avoid issuing INSERT OR IGNORE on every blog API
    # request because even a no-op insert promotes SQLite to a write
    # transaction and can needlessly contend with posting/comment traffic.
    existing_rows = conn.execute(
        """
        SELECT section_key, name, short_name, description, sort_order, source_keywords_json
        FROM blog_sections
        """
    ).fetchall()
    existing_by_key = {str(row["section_key"]): dict(row) for row in existing_rows}
    existing_keys = set(existing_by_key)

    defaults_by_key = {section["section_key"]: section for section in DEFAULT_BLOG_SECTIONS}
    for section_key, legacy_identity in LEGACY_DEFAULT_SECTION_IDENTITIES.items():
        row = existing_by_key.get(section_key)
        section = defaults_by_key.get(section_key)
        if not row or not section:
            continue
        current_identity = (
            str(row.get("name") or ""),
            str(row.get("short_name") or ""),
            int(row.get("sort_order") or 0),
        )
        if current_identity != legacy_identity:
            continue
        if section_key == DEFAULT_BLOG_SECTION_KEY:
            source_keywords_json = str(row.get("source_keywords_json") or "[]")
            if not _safe_json_list(source_keywords_json):
                source_keywords_json = _json_dumps(section.get("source_keywords") or [])
            current_description = str(row.get("description") or "")
            upgraded_description = (
                section["description"]
                if current_description == "课堂之外的灵感、作品、校园观察与成长记录。"
                else current_description
            )
            conn.execute(
                """
                UPDATE blog_sections
                SET name = ?, short_name = ?, description = ?, sort_order = ?,
                    source_keywords_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE section_key = ?
                """,
                (
                    section["name"],
                    section["short_name"],
                    upgraded_description,
                    int(section["sort_order"]),
                    source_keywords_json,
                    section_key,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE blog_sections
                SET sort_order = ?, updated_at = CURRENT_TIMESTAMP
                WHERE section_key = ?
                """,
                (int(section["sort_order"]), section_key),
            )
    missing_sections = [
        section for section in DEFAULT_BLOG_SECTIONS if section["section_key"] not in existing_keys
    ]
    if not missing_sections:
        return

    engine = get_configured_db_engine()
    insert_sql = """
        INSERT INTO blog_sections (
            section_key, name, short_name, description, icon, accent_color,
            sort_order, is_enabled, is_career, allow_user_posts,
            source_keywords_json, source_templates_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """
    if engine == "postgres":
        insert_sql = f"{insert_sql} ON CONFLICT (section_key) DO NOTHING"
    else:
        insert_sql = insert_sql.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)

    for section in missing_sections:
        conn.execute(
            insert_sql,
            (
                section["section_key"],
                section["name"],
                section["short_name"],
                section["description"],
                section["icon"],
                section["accent_color"],
                int(section["sort_order"]),
                1 if section.get("is_career") else 0,
                _json_dumps(section.get("source_keywords") or []),
                _json_dumps(section.get("source_templates") or []),
            ),
        )


def list_blog_sections(
    conn,
    *,
    include_disabled: bool = False,
    include_source_config: bool = False,
) -> list[dict[str, Any]]:
    ensure_default_blog_sections(conn)
    conditions = [] if include_disabled else ["is_enabled = 1"]
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT section_key, name, short_name, description, icon, accent_color,
               sort_order, is_enabled, is_career, allow_user_posts,
               source_keywords_json, source_templates_json, created_at, updated_at
        FROM blog_sections
        {where_sql}
        ORDER BY sort_order ASC, section_key ASC
        """
    ).fetchall()

    sections: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        section = {
            "section_key": str(data.get("section_key") or DEFAULT_BLOG_SECTION_KEY),
            "name": str(data.get("name") or "未命名板块"),
            "short_name": str(data.get("short_name") or data.get("name") or "板块"),
            "description": str(data.get("description") or ""),
            "icon": str(data.get("icon") or "•"),
            "accent_color": str(data.get("accent_color") or "#2563eb"),
            "sort_order": int(data.get("sort_order") or 100),
            "is_enabled": bool(data.get("is_enabled")),
            "is_career": bool(data.get("is_career")),
            "allow_user_posts": bool(data.get("allow_user_posts")),
        }
        if include_source_config:
            section["source_keywords"] = [
                str(item).strip()
                for item in _safe_json_list(data.get("source_keywords_json"))
                if str(item).strip()
            ]
            section["source_templates"] = [
                item for item in _safe_json_list(data.get("source_templates_json")) if isinstance(item, dict)
            ]
        sections.append(section)
    return sections


def resolve_blog_section_key(
    conn,
    value: Any,
    *,
    fallback: str | None = DEFAULT_BLOG_SECTION_KEY,
    require_user_posts: bool = False,
) -> str | None:
    raw_key = str(value or "").strip().lower()
    if not raw_key:
        raw_key = str(fallback or "").strip().lower()
    if not raw_key:
        return None
    if not SECTION_KEY_PATTERN.fullmatch(raw_key):
        raise ValueError("博客板块参数不正确")

    ensure_default_blog_sections(conn)
    conditions = ["section_key = ?", "is_enabled = 1"]
    params: list[Any] = [raw_key]
    if require_user_posts:
        conditions.append("allow_user_posts = 1")
    row = conn.execute(
        f"SELECT section_key FROM blog_sections WHERE {' AND '.join(conditions)} LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        raise ValueError("博客板块不存在或暂不可用")
    return str(row["section_key"])


def save_blog_section(conn, payload: dict[str, Any], *, section_key: str | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    normalized_key = str(section_key or data.get("section_key") or "").strip().lower()
    if not SECTION_KEY_PATTERN.fullmatch(normalized_key):
        raise ValueError("板块标识仅支持小写字母、数字、连字符和下划线，且必须以字母开头")
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("板块名称不能为空")
    short_name = str(data.get("short_name") or name).strip()[:16]
    description = str(data.get("description") or "").strip()[:300]
    icon = str(data.get("icon") or "•").strip()[:12] or "•"
    accent_color = str(data.get("accent_color") or "#2563eb").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", accent_color):
        raise ValueError("板块主题色必须是六位十六进制颜色")
    try:
        sort_order = max(0, min(int(data.get("sort_order") or 100), 9999))
    except (TypeError, ValueError):
        sort_order = 100
    is_enabled = bool(data.get("is_enabled", True))
    is_career = bool(data.get("is_career", False))
    allow_user_posts = bool(data.get("allow_user_posts", True))
    if normalized_key == DEFAULT_BLOG_SECTION_KEY and not is_enabled:
        raise ValueError("默认杂谈板块不能停用")

    source_keywords = [
        str(item).strip()[:100]
        for item in _safe_json_list(data.get("source_keywords"))
        if str(item).strip()
    ][:30]
    source_templates = []
    for item in _safe_json_list(data.get("source_templates"))[:20]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()[:2000]
        if not url.startswith(("http://", "https://")):
            continue
        source_templates.append(
            {
                "name": str(item.get("name") or "自定义信息源").strip()[:100],
                "url": url,
                "kind": str(item.get("kind") or "keyword_rss").strip()[:30],
                "requires_keyword_match": bool(item.get("requires_keyword_match", True)),
                "query_suffix": str(item.get("query_suffix") or "").strip()[:500],
            }
        )

    now_sql = "CURRENT_TIMESTAMP"
    existing = conn.execute(
        "SELECT section_key FROM blog_sections WHERE section_key = ? LIMIT 1",
        (normalized_key,),
    ).fetchone()
    if existing:
        conn.execute(
            f"""
            UPDATE blog_sections
            SET name = ?, short_name = ?, description = ?, icon = ?, accent_color = ?,
                sort_order = ?, is_enabled = ?, is_career = ?, allow_user_posts = ?,
                source_keywords_json = ?, source_templates_json = ?, updated_at = {now_sql}
            WHERE section_key = ?
            """,
            (
                name, short_name, description, icon, accent_color, sort_order,
                1 if is_enabled else 0, 1 if is_career else 0, 1 if allow_user_posts else 0,
                _json_dumps(source_keywords), _json_dumps(source_templates), normalized_key,
            ),
        )
    else:
        conn.execute(
            f"""
            INSERT INTO blog_sections (
                section_key, name, short_name, description, icon, accent_color, sort_order,
                is_enabled, is_career, allow_user_posts, source_keywords_json,
                source_templates_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})
            """,
            (
                normalized_key, name, short_name, description, icon, accent_color, sort_order,
                1 if is_enabled else 0, 1 if is_career else 0, 1 if allow_user_posts else 0,
                _json_dumps(source_keywords), _json_dumps(source_templates),
            ),
        )
    section = next(
        item
        for item in list_blog_sections(conn, include_disabled=True, include_source_config=True)
        if item["section_key"] == normalized_key
    )
    return section
