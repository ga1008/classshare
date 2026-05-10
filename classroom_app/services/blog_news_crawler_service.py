from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import os
import random
import re
import socket
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from PIL import Image, UnidentifiedImageError

from ..config import AI_ASSISTANT_URL
from ..database import get_db_connection
from .blog_service import (
    AUTHOR_DISPLAY_REAL,
    POST_STATUS_DRAFT,
    POST_STATUS_PUBLISHED,
    VISIBILITY_PUBLIC,
    create_post,
    register_media_asset,
)
from .file_service import global_file_write_path, resolve_global_file_path


ASSISTANT_USER = {
    "id": 0,
    "role": "assistant",
    "name": "AI管家",
    "nickname": "AI管家",
}

RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_SKIPPED = "skipped"

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; LanShareAIBlogCrawler/1.0; course-news-curator)"
)
SOURCE_KIND_KEYWORD_RSS = "keyword_rss"
SOURCE_KIND_FIXED_RSS = "fixed_rss"
MAX_AI_CANDIDATES = 80
MAX_AI_TEXT_CHARS = 1600

DEFAULT_DOMESTIC_SOURCE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {"name": "Baidu Tech News", "url": "https://news.baidu.com/n?cmd=1&class=technnews&tn=rss", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "Baidu Education News", "url": "https://news.baidu.com/n?cmd=1&class=edunews&tn=rss", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "ChinaNews Live", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "ChinaNews Education", "url": "https://www.chinanews.com.cn/rss/edu.xml", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "IT Home", "url": "https://www.ithome.com/rss/", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "InfoQ China", "url": "https://www.infoq.cn/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "SegmentFault", "url": "https://segmentfault.com/feeds", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "V2EX Tech", "url": "https://www.v2ex.com/feed/tab/tech.xml", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "SSPai", "url": "https://sspai.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "36Kr", "url": "https://36kr.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "GeekPark", "url": "https://www.geekpark.net/rss", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "QbitAI", "url": "https://www.qbitai.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "Leiphone", "url": "https://www.leiphone.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "TMTPost", "url": "https://www.tmtpost.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "Solidot", "url": "https://www.solidot.org/index.rss", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "4hou Security", "url": "https://www.4hou.com/feed", "kind": SOURCE_KIND_FIXED_RSS},
    {"name": "SecWiki", "url": "https://www.sec-wiki.com/news/rss", "kind": SOURCE_KIND_FIXED_RSS},
)

GLOBAL_FALLBACK_SOURCE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "Bing News",
        "url": "https://www.bing.com/news/search?q={{keyword_q}}&format=RSS&setlang=zh-CN&cc=CN&freshness={{bing_freshness}}",
        "kind": SOURCE_KIND_KEYWORD_RSS,
        "requires_keyword_match": False,
    },
    {
        "name": "Google News",
        "url": "https://news.google.com/rss/search?q={{keyword_q}}+when:{{recent_days}}d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "kind": SOURCE_KIND_KEYWORD_RSS,
        "requires_keyword_match": False,
    },
)

IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

KEYWORD_SPLIT_PATTERN = re.compile(r"[\s,，;；、/|#\[\]（）(){}<>《》]+")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
MARKDOWN_IMAGE_TOKEN_PATTERN = re.compile(r"\{\{\s*image[_-]?(\d+)\s*\}\}", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json_loads(raw_value: Any, fallback: Any) -> Any:
    if isinstance(raw_value, type(fallback)):
        return raw_value
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strip_html(value: Any) -> str:
    text = HTML_TAG_PATTERN.sub(" ", str(value or ""))
    return _normalize_space(html_lib.unescape(text))


def _truncate(value: Any, limit: int) -> str:
    text = _normalize_space(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _canonicalize_url(url: str, base_url: str = "") -> str:
    raw_url = str(url or "").strip()
    if base_url:
        raw_url = urljoin(base_url, raw_url)
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    clean_query_parts = []
    for part in parsed.query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key.startswith("utm_") or key in {"spm", "from", "rss", "ocid", "ns_mchannel"}:
            continue
        clean_query_parts.append(part)

    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            "&".join(clean_query_parts),
            "",
        )
    )


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        pass
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _format_date_for_humans(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value or "")
    return parsed.strftime("%Y-%m-%d %H:%M")


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "Asia/Shanghai").strip() or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _normalize_time_text(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw)
    if not match:
        return fallback
    hour = int(match.group(1))
    minute = int(match.group(2))
    return f"{hour:02d}:{minute:02d}"


def _split_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = KEYWORD_SPLIT_PATTERN.split(str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = str(part or "").strip()
        if not keyword or len(keyword) < 2 or len(keyword) > 40:
            continue
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(keyword)
    return result


def _normalize_source_template(raw_template: Any) -> dict[str, Any] | None:
    if isinstance(raw_template, str):
        line = raw_template.strip()
        if not line or line.startswith("#"):
            return None
        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 1:
            url = parts[0]
            name = _domain_from_url(url) or "Custom RSS"
            kind = SOURCE_KIND_KEYWORD_RSS if "{{keyword" in url else SOURCE_KIND_FIXED_RSS
            match_flag = ""
        else:
            name = parts[0] or "Custom RSS"
            url = parts[1] if len(parts) > 1 else ""
            kind = parts[2] if len(parts) > 2 else ""
            match_flag = parts[3] if len(parts) > 3 else ""
        raw_template = {"name": name, "url": url, "kind": kind, "match": match_flag}
    if not isinstance(raw_template, dict):
        return None

    url = str(raw_template.get("url") or "").strip()
    name = str(raw_template.get("name") or _domain_from_url(url) or "Custom RSS").strip()
    if not url or not name:
        return None
    kind = str(raw_template.get("kind") or "").strip().lower()
    if kind not in {SOURCE_KIND_KEYWORD_RSS, SOURCE_KIND_FIXED_RSS}:
        kind = SOURCE_KIND_KEYWORD_RSS if "{{keyword" in url else SOURCE_KIND_FIXED_RSS
    default_match = kind == SOURCE_KIND_FIXED_RSS
    match_value = raw_template.get("requires_keyword_match", raw_template.get("match", default_match))
    if isinstance(match_value, str) and match_value.strip().lower() in {"all", "no", "false", "0", "none"}:
        requires_keyword_match = False
    else:
        requires_keyword_match = _safe_bool(match_value, default_match)
    return {
        "name": _truncate(name, 80),
        "url": url[:1000],
        "kind": kind,
        "requires_keyword_match": requires_keyword_match,
    }


def _normalize_source_templates(raw_templates: Any) -> list[dict[str, Any]]:
    if isinstance(raw_templates, str):
        values: list[Any] = [line for line in raw_templates.splitlines()]
    elif isinstance(raw_templates, list):
        values = raw_templates
    else:
        values = []
    templates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_template in values:
        template = _normalize_source_template(raw_template)
        if not template:
            continue
        key = (template["name"].lower(), template["url"])
        if key in seen:
            continue
        seen.add(key)
        templates.append(template)
        if len(templates) >= 80:
            break
    return templates


def _source_templates_to_text(templates: list[dict[str, Any]]) -> str:
    lines = []
    for template in templates:
        suffix = "match" if template.get("requires_keyword_match") else "all"
        lines.append(f"{template.get('name')} | {template.get('url')} | {template.get('kind') or SOURCE_KIND_FIXED_RSS} | {suffix}")
    return "\n".join(lines)


def _effective_source_templates(config: dict[str, Any]) -> list[dict[str, Any]]:
    templates = [dict(item) for item in DEFAULT_DOMESTIC_SOURCE_TEMPLATES]
    templates.extend(_normalize_source_templates(config.get("custom_source_templates") or []))
    if config.get("enable_global_search_sources"):
        templates.extend(dict(item) for item in GLOBAL_FALLBACK_SOURCE_TEMPLATES)

    normalized: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for template in templates:
        item = _normalize_source_template(template)
        if not item:
            continue
        dedupe_key = item["url"]
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        normalized.append(item)
    return normalized


def _keyword_match_terms(keyword: str, course_name: str = "") -> list[str]:
    terms: list[str] = []
    for value in (keyword, course_name):
        normalized = _normalize_space(value).lower()
        if len(normalized) >= 2:
            terms.append(normalized)
        chinese_only = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        if len(chinese_only) >= 4:
            for size in (2, 3):
                for index in range(0, len(chinese_only) - size + 1):
                    terms.append(chinese_only[index : index + size])
        for part in re.split(r"[\s/._\-+]+", normalized):
            if len(part) >= 2:
                terms.append(part)
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        unique_terms.append(term)
    return unique_terms


def _parsed_item_matches_keyword(parsed: dict[str, Any], keyword: str, course_name: str = "") -> bool:
    terms = _keyword_match_terms(keyword, course_name)
    if not terms:
        return True
    haystack = " ".join(
        [
            str(parsed.get("title") or ""),
            str(parsed.get("summary") or ""),
            str(parsed.get("source") or ""),
            str(parsed.get("url") or ""),
        ]
    ).lower()
    return any(term in haystack for term in terms)


def _content_fingerprint(title: str, summary: str, canonical_url: str) -> str:
    domain = _domain_from_url(canonical_url)
    normalized_title = re.sub(r"[\W_]+", "", str(title or "").lower())
    normalized_summary = re.sub(r"[\W_]+", "", str(summary or "").lower())[:120]
    return _hash_text("|".join([domain, normalized_title, normalized_summary]))


def _normalize_media(media_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in media_items:
        url = _canonicalize_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        media_type = str(item.get("type") or "").strip().lower()
        if not media_type:
            media_type = "image" if _looks_like_image_url(url) else "link"
        normalized.append(
            {
                "url": url,
                "type": media_type[:40],
                "mime_type": str(item.get("mime_type") or "").strip().lower()[:80],
                "caption": _truncate(item.get("caption") or item.get("title") or "", 120),
                "source": _truncate(item.get("source") or "", 120),
            }
        )
        if len(normalized) >= 8:
            break
    return normalized


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(str(url or "")).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))


@dataclass
class NewsCandidate:
    keyword: str
    course_names: list[str]
    source_name: str
    title: str
    url: str
    canonical_url: str
    summary: str
    published_at: str
    fetched_at: str
    media: list[dict[str, str]] = field(default_factory=list)
    page_excerpt: str = ""
    score: float = 0.0

    @property
    def url_hash(self) -> str:
        return _hash_text(self.canonical_url or self.url)

    @property
    def content_hash(self) -> str:
        return _content_fingerprint(self.title, self.summary or self.page_excerpt, self.canonical_url or self.url)

    def as_raw_payload(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "course_names": self.course_names,
            "source_name": self.source_name,
            "title": self.title,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "summary": self.summary,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "media": self.media,
            "page_excerpt": self.page_excerpt,
            "score": round(float(self.score or 0.0), 3),
        }


@dataclass(frozen=True)
class NewsFeedSource:
    name: str
    url: str
    kind: str = SOURCE_KIND_FIXED_RSS
    requires_keyword_match: bool = True


class _NewsPageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.meta: dict[str, str] = {}
        self.canonical_url = ""
        self.media: list[dict[str, str]] = []
        self._in_paragraph = False
        self._paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {str(key or "").lower(): str(value or "") for key, value in attrs}
        lowered = tag.lower()
        if lowered == "meta":
            name = (attrs_map.get("property") or attrs_map.get("name") or "").strip().lower()
            content = attrs_map.get("content") or ""
            if name and content:
                self.meta[name] = html_lib.unescape(content).strip()
                if name in {"og:image", "twitter:image"}:
                    self.media.append({"type": "image", "url": urljoin(self.base_url, content), "source": "page-meta"})
                elif name in {"og:video", "twitter:player"}:
                    self.media.append({"type": "video", "url": urljoin(self.base_url, content), "source": "page-meta"})
        elif lowered == "link":
            rel = attrs_map.get("rel", "").lower()
            href = attrs_map.get("href", "")
            if "canonical" in rel and href:
                self.canonical_url = _canonicalize_url(href, self.base_url)
        elif lowered == "img":
            src = attrs_map.get("src") or attrs_map.get("data-src") or attrs_map.get("data-original") or ""
            if src:
                self.media.append(
                    {
                        "type": "image",
                        "url": urljoin(self.base_url, src),
                        "caption": attrs_map.get("alt", ""),
                        "source": "page-img",
                    }
                )
        elif lowered == "p":
            self._in_paragraph = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p":
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if not self._in_paragraph or len(self._paragraphs) >= 10:
            return
        text = _normalize_space(data)
        if len(text) >= 20:
            self._paragraphs.append(text)

    def page_summary(self) -> str:
        for key in ("description", "og:description", "twitter:description"):
            if self.meta.get(key):
                return _truncate(self.meta[key], 600)
        return _truncate(" ".join(self._paragraphs), 800)


class _PoliteDelay:
    def __init__(self, min_seconds: float, max_seconds: float):
        self.min_seconds = max(0.0, float(min_seconds or 0.0))
        self.max_seconds = max(self.min_seconds, float(max_seconds or self.min_seconds))
        self._last_by_host: dict[str, float] = {}

    async def wait_for(self, url: str) -> None:
        host = _domain_from_url(url)
        now = time.monotonic()
        last = self._last_by_host.get(host, 0.0)
        interval = random.uniform(self.min_seconds, self.max_seconds)
        delay = (last + interval) - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_by_host[host] = time.monotonic()


class _RobotsCache:
    def __init__(self, user_agent: str):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._cache: dict[str, RobotFileParser | None] = {}

    async def can_fetch(self, client: httpx.AsyncClient, url: str) -> bool:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._cache:
            parser = RobotFileParser()
            robots_url = f"{root}/robots.txt"
            try:
                response = await client.get(robots_url, timeout=6.0)
                if response.status_code >= 500:
                    self._cache[root] = None
                else:
                    parser.set_url(robots_url)
                    parser.parse(response.text.splitlines())
                    self._cache[root] = parser
            except httpx.HTTPError:
                self._cache[root] = None
        parser = self._cache.get(root)
        if parser is None:
            return True
        try:
            return bool(parser.can_fetch(self.user_agent, url))
        except Exception:
            return True


def load_blog_news_crawler_config(conn) -> dict[str, Any]:
    conn.execute("INSERT OR IGNORE INTO blog_news_crawler_config (id) VALUES (1)")
    row = conn.execute("SELECT * FROM blog_news_crawler_config WHERE id = 1").fetchone()
    data = dict(row) if row else {}
    custom_source_templates = _normalize_source_templates(
        _safe_json_loads(data.get("source_templates_json") if "source_templates_json" in data else None, [])
    )
    enable_global_search_sources = _safe_bool(data.get("enable_global_search_sources") if "enable_global_search_sources" in data else 0, False)
    base_config: dict[str, Any] = {
        "custom_source_templates": custom_source_templates,
        "custom_source_template_text": _source_templates_to_text(custom_source_templates),
        "enable_global_search_sources": enable_global_search_sources,
    }
    source_templates = _effective_source_templates(base_config)
    return {
        "enabled": _safe_bool(data.get("enabled"), True),
        "auto_publish": _safe_bool(data.get("auto_publish"), True),
        "featured_posts": _safe_bool(data.get("featured_posts"), True),
        "timezone": str(data.get("timezone") or "Asia/Shanghai"),
        "schedule_window_start": _normalize_time_text(data.get("schedule_window_start"), "01:20"),
        "schedule_window_end": _normalize_time_text(data.get("schedule_window_end"), "04:40"),
        "recent_days": max(1, min(_safe_int(data.get("recent_days"), 1), 7)),
        "max_keywords": max(1, min(_safe_int(data.get("max_keywords"), 8), 50)),
        "search_limit_per_keyword": max(5, min(_safe_int(data.get("search_limit_per_keyword"), 20), 50)),
        "max_candidates_total": max(10, min(_safe_int(data.get("max_candidates_total"), 80), 200)),
        "max_posts_per_run": max(1, min(_safe_int(data.get("max_posts_per_run"), 2), 8)),
        "article_fetch_limit": max(0, min(_safe_int(data.get("article_fetch_limit"), 24), 80)),
        "fetch_article_pages": _safe_bool(data.get("fetch_article_pages"), True),
        "fetch_images": _safe_bool(data.get("fetch_images"), True),
        "max_images_per_post": max(0, min(_safe_int(data.get("max_images_per_post"), 1), 4)),
        "max_image_bytes": max(256 * 1024, min(_safe_int(data.get("max_image_bytes"), 6 * 1024 * 1024), 15 * 1024 * 1024)),
        "request_timeout_seconds": max(4.0, min(_safe_float(data.get("request_timeout_seconds"), 12.0), 45.0)),
        "min_request_interval_seconds": max(0.5, min(_safe_float(data.get("min_request_interval_seconds"), 2.0), 30.0)),
        "max_request_interval_seconds": max(0.5, min(_safe_float(data.get("max_request_interval_seconds"), 6.0), 60.0)),
        "extra_keywords": _safe_json_loads(data.get("extra_keywords_json"), []),
        "blocked_domains": _safe_json_loads(data.get("blocked_domains_json"), []),
        "custom_source_templates": custom_source_templates,
        "custom_source_template_text": _source_templates_to_text(custom_source_templates),
        "source_templates": source_templates,
        "source_count": len(source_templates),
        "enable_global_search_sources": enable_global_search_sources,
        "next_run_at": str(data.get("next_run_at") or ""),
        "last_run_id": _safe_int(data.get("last_run_id"), 0) or None,
        "last_run_at": str(data.get("last_run_at") or ""),
        "last_heartbeat_at": str(data.get("last_heartbeat_at") or ""),
        "worker_id": str(data.get("worker_id") or ""),
        "worker_status": str(data.get("worker_status") or ""),
        "updated_by_teacher_id": _safe_int(data.get("updated_by_teacher_id"), 0) or None,
        "updated_at": str(data.get("updated_at") or ""),
        "user_agent": os.getenv("BLOG_NEWS_CRAWLER_USER_AGENT", DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT,
    }


def update_blog_news_crawler_config(conn, payload: dict[str, Any], teacher_id: int | str | None = None) -> dict[str, Any]:
    current = load_blog_news_crawler_config(conn)
    extra_keywords = payload.get("extra_keywords", current.get("extra_keywords", []))
    blocked_domains = payload.get("blocked_domains", current.get("blocked_domains", []))
    source_templates = payload.get("source_templates", current.get("custom_source_templates", []))
    if isinstance(extra_keywords, str):
        extra_keywords = _split_keywords(extra_keywords.replace("\n", ","))
    if isinstance(blocked_domains, str):
        blocked_domains = [
            _domain_from_url(item if "://" in item else f"https://{item}") or str(item).strip().lower()
            for item in re.split(r"[\s,，;；]+", blocked_domains)
            if str(item).strip()
        ]
    custom_source_templates = _normalize_source_templates(source_templates)

    min_interval = max(0.5, min(_safe_float(payload.get("min_request_interval_seconds"), current["min_request_interval_seconds"]), 30.0))
    max_interval = max(min_interval, min(_safe_float(payload.get("max_request_interval_seconds"), current["max_request_interval_seconds"]), 60.0))

    values = {
        "enabled": 1 if _safe_bool(payload.get("enabled"), current["enabled"]) else 0,
        "auto_publish": 1 if _safe_bool(payload.get("auto_publish"), current["auto_publish"]) else 0,
        "featured_posts": 1 if _safe_bool(payload.get("featured_posts"), current["featured_posts"]) else 0,
        "timezone": str(payload.get("timezone") or current["timezone"] or "Asia/Shanghai").strip()[:80],
        "schedule_window_start": _normalize_time_text(payload.get("schedule_window_start"), current["schedule_window_start"]),
        "schedule_window_end": _normalize_time_text(payload.get("schedule_window_end"), current["schedule_window_end"]),
        "recent_days": max(1, min(_safe_int(payload.get("recent_days"), current["recent_days"]), 7)),
        "max_keywords": max(1, min(_safe_int(payload.get("max_keywords"), current["max_keywords"]), 50)),
        "search_limit_per_keyword": max(5, min(_safe_int(payload.get("search_limit_per_keyword"), current["search_limit_per_keyword"]), 50)),
        "max_candidates_total": max(10, min(_safe_int(payload.get("max_candidates_total"), current["max_candidates_total"]), 200)),
        "max_posts_per_run": max(1, min(_safe_int(payload.get("max_posts_per_run"), current["max_posts_per_run"]), 8)),
        "article_fetch_limit": max(0, min(_safe_int(payload.get("article_fetch_limit"), current["article_fetch_limit"]), 80)),
        "fetch_article_pages": 1 if _safe_bool(payload.get("fetch_article_pages"), current["fetch_article_pages"]) else 0,
        "fetch_images": 1 if _safe_bool(payload.get("fetch_images"), current["fetch_images"]) else 0,
        "max_images_per_post": max(0, min(_safe_int(payload.get("max_images_per_post"), current["max_images_per_post"]), 4)),
        "max_image_bytes": max(256 * 1024, min(_safe_int(payload.get("max_image_bytes"), current["max_image_bytes"]), 15 * 1024 * 1024)),
        "request_timeout_seconds": max(4.0, min(_safe_float(payload.get("request_timeout_seconds"), current["request_timeout_seconds"]), 45.0)),
        "min_request_interval_seconds": min_interval,
        "max_request_interval_seconds": max_interval,
        "extra_keywords_json": _json_dumps(_split_keywords(extra_keywords)),
        "blocked_domains_json": _json_dumps(sorted(set(str(item).strip().lower() for item in blocked_domains if str(item).strip()))),
        "source_templates_json": _json_dumps(custom_source_templates),
        "enable_global_search_sources": 1 if _safe_bool(payload.get("enable_global_search_sources"), current["enable_global_search_sources"]) else 0,
        "updated_by_teacher_id": _safe_int(teacher_id, 0) or None,
        "updated_at": _now_iso(),
    }
    assignments = ", ".join(f"{key} = ?" for key in values)
    conn.execute(
        f"UPDATE blog_news_crawler_config SET {assignments} WHERE id = 1",
        tuple(values.values()),
    )
    return load_blog_news_crawler_config(conn)


def load_course_news_keywords(conn, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = config or load_blog_news_crawler_config(conn)
    rows = conn.execute(
        """
        SELECT id, name, sect_name, description
        FROM courses
        ORDER BY id DESC
        """
    ).fetchall()
    keywords: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        course_name = str(row["name"] or "").strip()
        seed_values = [course_name, str(row["sect_name"] or "").strip()]
        description = str(row["description"] or "").strip()
        if description:
            seed_values.extend(_split_keywords(description[:240]))
        for keyword in seed_values:
            normalized = _normalize_space(keyword)
            if len(normalized) < 2 or len(normalized) > 40:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            keywords.append(
                {
                    "keyword": normalized,
                    "course_id": int(row["id"]),
                    "course_name": course_name,
                }
            )
    for keyword in _split_keywords(config.get("extra_keywords") or []):
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        keywords.append({"keyword": keyword, "course_id": None, "course_name": "全局补充"})
    return keywords[: int(config.get("max_keywords") or 8)]


def load_blog_news_crawler_dashboard(conn) -> dict[str, Any]:
    config = load_blog_news_crawler_config(conn)
    keywords = load_course_news_keywords(conn, config)
    recent_runs = [
        _serialize_run_row(row)
        for row in conn.execute(
            """
            SELECT *
            FROM blog_news_crawler_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 12
            """
        ).fetchall()
    ]
    recent_posts = [
        dict(row)
        for row in conn.execute(
            """
            SELECT i.id, i.keyword, i.title AS source_title, i.source_name,
                   i.published_at, i.post_id, p.title AS post_title, p.status AS post_status,
                   p.created_at AS post_created_at
            FROM blog_news_crawler_items i
            JOIN blog_posts p ON p.id = i.post_id
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 8
            """
        ).fetchall()
    ]
    pending_run = conn.execute(
        """
        SELECT *
        FROM blog_news_crawler_runs
        WHERE status IN ('pending', 'running')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    published_count = int(
        conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM blog_news_crawler_items
            WHERE post_id IS NOT NULL
            """
        ).fetchone()["cnt"]
        or 0
    )
    worker_stale = True
    heartbeat = _parse_datetime(config.get("last_heartbeat_at"))
    if heartbeat is not None:
        worker_stale = (_now() - heartbeat) > timedelta(minutes=5)
    return {
        "config": config,
        "keywords": keywords,
        "sources": config.get("source_templates") or [],
        "recent_runs": recent_runs,
        "recent_posts": recent_posts,
        "pending_run": _serialize_run_row(pending_run) if pending_run else None,
        "published_count": published_count,
        "worker_stale": worker_stale,
    }


def enqueue_blog_news_crawler_run(
    conn,
    *,
    trigger_source: str = TRIGGER_MANUAL,
    scheduled_for: str | None = None,
    worker_id: str = "",
) -> dict[str, Any]:
    existing = conn.execute(
        """
        SELECT *
        FROM blog_news_crawler_runs
        WHERE status IN ('pending', 'running')
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    if existing is not None and trigger_source == TRIGGER_MANUAL:
        return _serialize_run_row(existing)
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO blog_news_crawler_runs (
            trigger_source, status, scheduled_for, worker_id, created_at, updated_at
        )
        VALUES (?, 'pending', ?, ?, ?, ?)
        """,
        (
            trigger_source,
            str(scheduled_for or now),
            str(worker_id or ""),
            now,
            now,
        ),
    )
    run_id = int(cursor.lastrowid)
    row = conn.execute("SELECT * FROM blog_news_crawler_runs WHERE id = ?", (run_id,)).fetchone()
    return _serialize_run_row(row)


def cancel_pending_blog_news_crawler_runs(conn) -> int:
    now = _now_iso()
    cursor = conn.execute(
        """
        UPDATE blog_news_crawler_runs
        SET status = 'skipped', finished_at = ?, updated_at = ?, error_message = 'manual cancel'
        WHERE status = 'pending'
        """,
        (now, now),
    )
    return int(cursor.rowcount or 0)


def mark_blog_news_crawler_heartbeat(conn, *, worker_id: str, status: str) -> None:
    conn.execute(
        """
        UPDATE blog_news_crawler_config
        SET worker_id = ?, worker_status = ?, last_heartbeat_at = ?, updated_at = updated_at
        WHERE id = 1
        """,
        (str(worker_id or ""), str(status or "")[:80], _now_iso()),
    )


async def run_blog_news_crawler_job(run_id: int, *, worker_id: str = "") -> dict[str, Any]:
    logs: list[dict[str, Any]] = []
    worker_id = worker_id or _default_worker_id()

    def log(message: str, **extra: Any) -> None:
        entry = {"time": _now_iso(), "message": message}
        if extra:
            entry.update(extra)
        logs.append(entry)
        print(f"[BLOG_NEWS] run={run_id} {message} {extra if extra else ''}")

    try:
        with get_db_connection() as conn:
            run_row = conn.execute("SELECT * FROM blog_news_crawler_runs WHERE id = ?", (run_id,)).fetchone()
            if run_row is None:
                raise RuntimeError(f"crawler run {run_id} not found")
            trigger_source = str(run_row["trigger_source"] or TRIGGER_SCHEDULED)
            config = load_blog_news_crawler_config(conn)
            keywords = load_course_news_keywords(conn, config)
            now = _now_iso()
            conn.execute(
                """
                UPDATE blog_news_crawler_runs
                SET status = ?, worker_id = ?, started_at = ?, updated_at = ?, keywords_json = ?
                WHERE id = ?
                """,
                (
                    RUN_STATUS_RUNNING,
                    worker_id,
                    now,
                    now,
                    _json_dumps(keywords),
                    run_id,
                ),
            )
            mark_blog_news_crawler_heartbeat(conn, worker_id=worker_id, status="running")
            conn.commit()

        if not config.get("enabled") and trigger_source != TRIGGER_MANUAL:
            log("crawler disabled; scheduled run skipped")
            _finish_run(
                run_id,
                status=RUN_STATUS_SKIPPED,
                logs=logs,
                message="crawler disabled",
                worker_id=worker_id,
            )
            return {"status": RUN_STATUS_SKIPPED, "run_id": run_id}

        if not keywords:
            log("no course keywords found")
            _finish_run(
                run_id,
                status=RUN_STATUS_SKIPPED,
                logs=logs,
                message="no course keywords found",
                worker_id=worker_id,
            )
            return {"status": RUN_STATUS_SKIPPED, "run_id": run_id}

        log("collecting news candidates", keyword_count=len(keywords))
        candidates = await _collect_news_candidates(config, keywords)
        log("candidate collection complete", candidate_count=len(candidates))

        with get_db_connection() as conn:
            stored_candidates, duplicate_count = _store_candidates(conn, run_id, candidates)
            conn.execute(
                """
                UPDATE blog_news_crawler_runs
                SET candidate_count = ?, new_candidate_count = ?, duplicate_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (len(candidates), len(stored_candidates), duplicate_count, _now_iso(), run_id),
            )
            conn.commit()

        if not stored_candidates:
            log("all candidates were duplicates")
            _finish_run(
                run_id,
                status=RUN_STATUS_SUCCESS,
                logs=logs,
                worker_id=worker_id,
                counts={"candidate_count": len(candidates), "duplicate_count": duplicate_count},
            )
            return {"status": RUN_STATUS_SUCCESS, "run_id": run_id, "published_count": 0}

        selected_candidates = await _select_candidates_with_ai(config, stored_candidates, keywords, log)
        if not selected_candidates:
            log("AI selected no publishable candidates")
            _finish_run(
                run_id,
                status=RUN_STATUS_SUCCESS,
                logs=logs,
                worker_id=worker_id,
                counts={
                    "candidate_count": len(candidates),
                    "new_candidate_count": len(stored_candidates),
                    "duplicate_count": duplicate_count,
                },
            )
            return {"status": RUN_STATUS_SUCCESS, "run_id": run_id, "published_count": 0}

        post_payloads = await _rewrite_candidates_with_ai(config, selected_candidates, keywords, log)
        published_count, skipped_count = await _publish_rewritten_posts(config, post_payloads, selected_candidates, run_id, log)
        final_status = RUN_STATUS_SUCCESS if published_count > 0 else RUN_STATUS_PARTIAL
        _finish_run(
            run_id,
            status=final_status,
            logs=logs,
            worker_id=worker_id,
            counts={
                "candidate_count": len(candidates),
                "new_candidate_count": len(stored_candidates),
                "duplicate_count": duplicate_count,
                "selected_count": len(selected_candidates),
                "published_count": published_count,
                "skipped_count": skipped_count,
            },
        )
        return {
            "status": final_status,
            "run_id": run_id,
            "selected_count": len(selected_candidates),
            "published_count": published_count,
        }
    except Exception as exc:
        log("crawler run failed", error=str(exc))
        _finish_run(run_id, status=RUN_STATUS_FAILED, logs=logs, message=str(exc), worker_id=worker_id)
        return {"status": RUN_STATUS_FAILED, "run_id": run_id, "error": str(exc)}


async def process_due_blog_news_crawler_runs_once(*, worker_id: str = "") -> dict[str, Any]:
    worker_id = worker_id or _default_worker_id()
    with get_db_connection() as conn:
        _mark_stale_running_runs(conn)
        config = load_blog_news_crawler_config(conn)
        mark_blog_news_crawler_heartbeat(conn, worker_id=worker_id, status="polling")
        _ensure_scheduled_run(conn, config, worker_id=worker_id)
        now = _now_iso()
        row = conn.execute(
            """
            SELECT *
            FROM blog_news_crawler_runs
            WHERE status = 'pending'
              AND COALESCE(scheduled_for, '') <= ?
            ORDER BY scheduled_for ASC, created_at ASC, id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        conn.commit()

    if row is None:
        return {"status": "idle", "worker_id": worker_id}
    return await run_blog_news_crawler_job(int(row["id"]), worker_id=worker_id)


async def run_blog_news_crawler_worker_forever(*, worker_id: str = "", poll_seconds: int | None = None) -> None:
    worker_id = worker_id or _default_worker_id()
    poll_seconds = max(10, int(poll_seconds or os.getenv("BLOG_NEWS_CRAWLER_POLL_SECONDS", "60")))
    print(f"[BLOG_NEWS] worker started: {worker_id}")
    while True:
        try:
            await process_due_blog_news_crawler_runs_once(worker_id=worker_id)
        except Exception as exc:
            print(f"[BLOG_NEWS] worker loop error: {exc}")
            try:
                with get_db_connection() as conn:
                    mark_blog_news_crawler_heartbeat(conn, worker_id=worker_id, status=f"error: {exc}")
                    conn.commit()
            except Exception:
                pass
        await asyncio.sleep(poll_seconds)


def _serialize_run_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    data["id"] = _safe_int(data.get("id"), 0)
    data["keywords"] = _safe_json_loads(data.get("keywords_json"), [])
    data["log"] = _safe_json_loads(data.get("log_json"), [])
    return data


def _serialize_item_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["id"] = _safe_int(data.get("id"), 0)
    data["run_id"] = _safe_int(data.get("run_id"), 0)
    data["course_names"] = _safe_json_loads(data.get("course_names_json"), [])
    data["media"] = _safe_json_loads(data.get("media_json"), [])
    data["raw"] = _safe_json_loads(data.get("raw_json"), {})
    data["selected"] = bool(data.get("selected"))
    return data


def _finish_run(
    run_id: int,
    *,
    status: str,
    logs: list[dict[str, Any]],
    worker_id: str,
    message: str = "",
    counts: dict[str, int] | None = None,
) -> None:
    counts = counts or {}
    now = _now_iso()
    with get_db_connection() as conn:
        conn.execute(
            """
            UPDATE blog_news_crawler_runs
            SET status = ?,
                finished_at = ?,
                updated_at = ?,
                error_message = ?,
                log_json = ?,
                candidate_count = COALESCE(?, candidate_count),
                new_candidate_count = COALESCE(?, new_candidate_count),
                duplicate_count = COALESCE(?, duplicate_count),
                selected_count = COALESCE(?, selected_count),
                published_count = COALESCE(?, published_count),
                skipped_count = COALESCE(?, skipped_count)
            WHERE id = ?
            """,
            (
                status,
                now,
                now,
                str(message or "")[:2000],
                _json_dumps(logs[-80:]),
                counts.get("candidate_count"),
                counts.get("new_candidate_count"),
                counts.get("duplicate_count"),
                counts.get("selected_count"),
                counts.get("published_count"),
                counts.get("skipped_count"),
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE blog_news_crawler_config
            SET last_run_id = ?,
                last_run_at = ?,
                next_run_at = CASE WHEN ? IN ('success', 'partial', 'skipped', 'failed') THEN '' ELSE next_run_at END
            WHERE id = 1
            """,
            (run_id, now, status),
        )
        mark_blog_news_crawler_heartbeat(conn, worker_id=worker_id, status=status)
        conn.commit()


def _mark_stale_running_runs(conn) -> None:
    cutoff = (_now() - timedelta(hours=6)).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE blog_news_crawler_runs
        SET status = 'failed', finished_at = ?, updated_at = ?, error_message = 'stale running job reclaimed'
        WHERE status = 'running'
          AND COALESCE(started_at, created_at) < ?
        """,
        (_now_iso(), _now_iso(), cutoff),
    )


def _ensure_scheduled_run(conn, config: dict[str, Any], *, worker_id: str) -> None:
    if not config.get("enabled"):
        return
    existing = conn.execute(
        """
        SELECT id
        FROM blog_news_crawler_runs
        WHERE status IN ('pending', 'running')
        LIMIT 1
        """
    ).fetchone()
    if existing is not None:
        return

    next_run_at = _parse_datetime(config.get("next_run_at"))
    tz = _timezone(config.get("timezone") or "Asia/Shanghai")
    now_local = datetime.now(tz).replace(tzinfo=None)
    if next_run_at is None:
        next_run_at = _choose_next_run_time(config, now_local=now_local)
        conn.execute(
            "UPDATE blog_news_crawler_config SET next_run_at = ? WHERE id = 1",
            (next_run_at.isoformat(timespec="seconds"),),
        )
        return

    if next_run_at <= now_local:
        enqueue_blog_news_crawler_run(
            conn,
            trigger_source=TRIGGER_SCHEDULED,
            scheduled_for=_now_iso(),
            worker_id=worker_id,
        )
        next_planned = _choose_next_run_time(config, now_local=now_local + timedelta(minutes=5))
        conn.execute(
            "UPDATE blog_news_crawler_config SET next_run_at = ? WHERE id = 1",
            (next_planned.isoformat(timespec="seconds"),),
        )


def _choose_next_run_time(config: dict[str, Any], *, now_local: datetime) -> datetime:
    start_text = _normalize_time_text(config.get("schedule_window_start"), "01:20")
    end_text = _normalize_time_text(config.get("schedule_window_end"), "04:40")
    start_hour, start_minute = [int(part) for part in start_text.split(":")]
    end_hour, end_minute = [int(part) for part in end_text.split(":")]
    target_day = now_local.date()
    start_at = datetime.combine(target_day, datetime.min.time()).replace(hour=start_hour, minute=start_minute)
    end_at = datetime.combine(target_day, datetime.min.time()).replace(hour=end_hour, minute=end_minute)
    if end_at <= start_at:
        end_at += timedelta(days=1)
    if now_local > end_at:
        start_at += timedelta(days=1)
        end_at += timedelta(days=1)
    elif now_local > start_at:
        start_at = now_local + timedelta(minutes=3)
    total_seconds = max(60, int((end_at - start_at).total_seconds()))
    return start_at + timedelta(seconds=random.randint(0, total_seconds))


async def _collect_news_candidates(config: dict[str, Any], keywords: list[dict[str, Any]]) -> list[NewsCandidate]:
    timeout = httpx.Timeout(float(config.get("request_timeout_seconds") or 12.0))
    headers = {
        "User-Agent": config.get("user_agent") or DEFAULT_USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
    }
    delay = _PoliteDelay(
        float(config.get("min_request_interval_seconds") or 2.0),
        float(config.get("max_request_interval_seconds") or 6.0),
    )
    robots = _RobotsCache(headers["User-Agent"])
    blocked_domains = {str(item).strip().lower() for item in config.get("blocked_domains") or []}
    candidates: list[NewsCandidate] = []
    seen_hashes: set[str] = set()
    per_keyword_limit = int(config.get("search_limit_per_keyword") or 20)
    feed_cache: dict[str, list[dict[str, Any]]] = {}

    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for keyword_entry in keywords:
            keyword = str(keyword_entry.get("keyword") or "").strip()
            if not keyword:
                continue
            course_name = str(keyword_entry.get("course_name") or "").strip()
            keyword_candidates: list[NewsCandidate] = []
            for source in _build_search_feed_urls(keyword, int(config.get("recent_days") or 1), config):
                if _domain_from_url(source.url) in blocked_domains:
                    continue
                if source.url in feed_cache:
                    parsed_items = feed_cache[source.url]
                else:
                    await delay.wait_for(source.url)
                    try:
                        response = await client.get(source.url)
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        print(f"[BLOG_NEWS] feed fetch failed source={source.name} keyword={keyword}: {exc}")
                        feed_cache[source.url] = []
                        continue
                    parsed_items = _parse_feed_items(response.text, source_name=source.name)
                    feed_cache[source.url] = parsed_items

                for parsed in parsed_items:
                    if len(keyword_candidates) >= per_keyword_limit:
                        break
                    if source.requires_keyword_match and not _parsed_item_matches_keyword(parsed, keyword, course_name):
                        continue
                    canonical_url = _canonicalize_url(parsed.get("url") or "")
                    if not canonical_url or _domain_from_url(canonical_url) in blocked_domains:
                        continue
                    title = _truncate(parsed.get("title") or "", 220)
                    if not title:
                        continue
                    summary = _truncate(parsed.get("summary") or "", 700)
                    candidate = NewsCandidate(
                        keyword=keyword,
                        course_names=[course_name] if course_name else [],
                        source_name=_truncate(parsed.get("source") or source.name, 120),
                        title=title,
                        url=canonical_url,
                        canonical_url=canonical_url,
                        summary=summary,
                        published_at=str(parsed.get("published_at") or ""),
                        fetched_at=_now_iso(),
                        media=_normalize_media(parsed.get("media") or []),
                    )
                    candidate.score = _score_candidate(candidate, config)
                    if not _is_recent_enough(candidate, int(config.get("recent_days") or 1)):
                        continue
                    unique_key = f"{candidate.url_hash}:{candidate.content_hash}"
                    if unique_key in seen_hashes:
                        continue
                    seen_hashes.add(unique_key)
                    keyword_candidates.append(candidate)
                if len(keyword_candidates) >= per_keyword_limit:
                    break
            candidates.extend(keyword_candidates)

        candidates.sort(key=lambda item: item.score, reverse=True)
        if config.get("fetch_article_pages") and int(config.get("article_fetch_limit") or 0) > 0:
            candidates = await _enrich_candidates_from_pages(
                candidates,
                client=client,
                delay=delay,
                robots=robots,
                limit=int(config.get("article_fetch_limit") or 0),
                blocked_domains=blocked_domains,
            )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[: int(config.get("max_candidates_total") or 80)]


def _build_search_feed_urls(keyword: str, recent_days: int, config: dict[str, Any] | None = None) -> list[NewsFeedSource]:
    encoded = quote_plus(keyword)
    replacements = {
        "{{keyword}}": encoded,
        "{{keyword_q}}": encoded,
        "{{keyword_plus}}": encoded,
        "{{keyword_raw}}": keyword,
        "{{recent_days}}": str(max(1, recent_days)),
        "{{bing_freshness}}": "Day" if recent_days <= 1 else "Week",
    }
    sources: list[NewsFeedSource] = []
    for template in _effective_source_templates(config or {}):
        raw_url = str(template.get("url") or "")
        for token, value in replacements.items():
            raw_url = raw_url.replace(token, value)
        url = _canonicalize_url(raw_url)
        if not url:
            continue
        sources.append(
            NewsFeedSource(
                name=str(template.get("name") or _domain_from_url(url) or "RSS"),
                url=url,
                kind=str(template.get("kind") or SOURCE_KIND_FIXED_RSS),
                requires_keyword_match=_safe_bool(
                    template.get("requires_keyword_match"),
                    str(template.get("kind") or SOURCE_KIND_FIXED_RSS) == SOURCE_KIND_FIXED_RSS,
                ),
            )
        )
    return sources


def _parse_feed_items(feed_text: str, *, source_name: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(feed_text.encode("utf-8") if isinstance(feed_text, str) else feed_text)
    except ElementTree.ParseError:
        return []

    items = [item for item in root.iter() if _local_name(item.tag) in {"item", "entry"}]
    parsed_items: list[dict[str, Any]] = []
    for item in items:
        title = _xml_child_text(item, "title")
        link = _xml_child_text(item, "link")
        if not link:
            link = _xml_link_href(item)
        summary = _xml_child_text(item, "description") or _xml_child_text(item, "summary") or _xml_child_text(item, "content")
        published_at = (
            _xml_child_text(item, "pubDate")
            or _xml_child_text(item, "published")
            or _xml_child_text(item, "updated")
            or _xml_child_text(item, "dc:date")
        )
        media = _xml_media_items(item)
        source = _xml_child_text(item, "source") or source_name
        parsed_items.append(
            {
                "title": _strip_html(title),
                "url": link,
                "summary": _strip_html(summary),
                "published_at": published_at,
                "source": source,
                "media": media,
            }
        )
    return parsed_items


def _local_name(tag: Any) -> str:
    text = str(tag or "")
    if "}" in text:
        text = text.rsplit("}", 1)[-1]
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text


def _xml_child_text(element: ElementTree.Element, child_name: str) -> str:
    wanted = child_name.rsplit(":", 1)[-1]
    for child in list(element):
        if _local_name(child.tag) == wanted:
            return _normalize_space("".join(child.itertext()))
    return ""


def _xml_link_href(element: ElementTree.Element) -> str:
    for child in list(element):
        if _local_name(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
    return ""


def _xml_media_items(element: ElementTree.Element) -> list[dict[str, str]]:
    media: list[dict[str, str]] = []
    for child in element.iter():
        local = _local_name(child.tag)
        if local in {"content", "thumbnail", "enclosure"}:
            url = child.attrib.get("url") or child.attrib.get("href")
            if not url:
                continue
            mime_type = (child.attrib.get("type") or "").lower()
            medium = (child.attrib.get("medium") or "").lower()
            media_type = "image" if "image" in mime_type or medium == "image" or local == "thumbnail" else "link"
            if "video" in mime_type or medium == "video":
                media_type = "video"
            media.append(
                {
                    "type": media_type,
                    "url": url,
                    "mime_type": mime_type,
                    "caption": child.attrib.get("title") or "",
                    "source": "feed",
                }
            )
    return media


async def _enrich_candidates_from_pages(
    candidates: list[NewsCandidate],
    *,
    client: httpx.AsyncClient,
    delay: _PoliteDelay,
    robots: _RobotsCache,
    limit: int,
    blocked_domains: set[str],
) -> list[NewsCandidate]:
    enriched: list[NewsCandidate] = []
    fetched_count = 0
    for candidate in candidates:
        if fetched_count >= limit:
            enriched.append(candidate)
            continue
        url = candidate.canonical_url or candidate.url
        domain = _domain_from_url(url)
        if domain in blocked_domains:
            enriched.append(candidate)
            continue
        if not await robots.can_fetch(client, url):
            enriched.append(candidate)
            continue
        await delay.wait_for(url)
        fetched_count += 1
        try:
            response = await client.get(url)
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code >= 400 or "text/html" not in content_type:
                enriched.append(candidate)
                continue
        except httpx.HTTPError:
            enriched.append(candidate)
            continue
        parser = _NewsPageParser(str(response.url))
        try:
            parser.feed(response.text[:600_000])
        except Exception:
            enriched.append(candidate)
            continue
        if parser.canonical_url:
            candidate.canonical_url = parser.canonical_url
        page_summary = parser.page_summary()
        if page_summary and len(page_summary) > len(candidate.summary):
            candidate.page_excerpt = page_summary
            candidate.summary = _truncate(page_summary, 700)
        if parser.media:
            candidate.media = _normalize_media([*candidate.media, *parser.media])
        candidate.score = _score_candidate(candidate, {})
        enriched.append(candidate)
    return enriched


def _is_recent_enough(candidate: NewsCandidate, recent_days: int) -> bool:
    parsed = _parse_datetime(candidate.published_at)
    if parsed is None:
        return True
    return parsed >= (_now() - timedelta(days=max(1, recent_days) + 1))


def _score_candidate(candidate: NewsCandidate, config: dict[str, Any]) -> float:
    score = 40.0
    title_lower = candidate.title.lower()
    keyword_lower = candidate.keyword.lower()
    if keyword_lower and keyword_lower in title_lower:
        score += 24.0
    if candidate.summary and keyword_lower in candidate.summary.lower():
        score += 8.0
    if candidate.media:
        score += 6.0
    published = _parse_datetime(candidate.published_at)
    if published is not None:
        age_hours = max(0.0, (_now() - published).total_seconds() / 3600.0)
        score += max(0.0, 36.0 - age_hours)
    else:
        score += 8.0
    if len(candidate.summary) >= 80:
        score += 4.0
    return score


def _store_candidates(conn, run_id: int, candidates: list[NewsCandidate]) -> tuple[list[dict[str, Any]], int]:
    stored: list[dict[str, Any]] = []
    duplicate_count = 0
    reusable_ids: set[int] = set()
    for candidate in candidates:
        existing = conn.execute(
            """
            SELECT *
            FROM blog_news_crawler_items
            WHERE url_hash = ? OR content_hash = ?
            ORDER BY post_id DESC, id DESC
            LIMIT 1
            """,
            (candidate.url_hash, candidate.content_hash),
        ).fetchone()
        if existing is not None:
            existing_item = _serialize_item_row(existing)
            if existing_item.get("post_id"):
                duplicate_count += 1
                continue
            existing_id = int(existing_item.get("id") or 0)
            if existing_id and existing_id not in reusable_ids:
                reusable_ids.add(existing_id)
                stored.append(existing_item)
            continue
        now = _now_iso()
        try:
            cursor = conn.execute(
                """
                INSERT INTO blog_news_crawler_items (
                    run_id, keyword, course_names_json, source_name, title, url, canonical_url,
                    url_hash, content_hash, summary, published_at, fetched_at, media_json,
                    score, raw_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    candidate.keyword,
                    _json_dumps(candidate.course_names),
                    candidate.source_name,
                    candidate.title,
                    candidate.url,
                    candidate.canonical_url,
                    candidate.url_hash,
                    candidate.content_hash,
                    candidate.summary,
                    candidate.published_at,
                    candidate.fetched_at,
                    _json_dumps(candidate.media),
                    float(candidate.score or 0.0),
                    _json_dumps(candidate.as_raw_payload()),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            duplicate_count += 1
            continue
        row = conn.execute("SELECT * FROM blog_news_crawler_items WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        if row is not None:
            stored.append(_serialize_item_row(row))
    return stored, duplicate_count


async def _select_candidates_with_ai(
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    log,
) -> list[dict[str, Any]]:
    max_posts = int(config.get("max_posts_per_run") or 2)
    candidate_lines = []
    limited = candidates[: min(MAX_AI_CANDIDATES, int(config.get("max_candidates_total") or MAX_AI_CANDIDATES))]
    for item in limited:
        media = item.get("media") or []
        candidate_lines.append(
            "\n".join(
                [
                    f"ID: {item['id']}",
                    f"关键词: {item.get('keyword')}",
                    f"标题: {item.get('title')}",
                    f"来源: {item.get('source_name')} / {_domain_from_url(item.get('canonical_url') or item.get('url'))}",
                    f"发布时间: {_format_date_for_humans(item.get('published_at')) or '未知'}",
                    f"摘要: {_truncate(item.get('summary'), 360)}",
                    f"媒体: {'有配图或视频' if media else '无'}",
                    f"链接: {item.get('canonical_url') or item.get('url')}",
                ]
            )
        )
    interest_keywords = "、".join(str(item.get("keyword") or "") for item in keywords[:30])
    candidate_text = "\n\n---\n\n".join(candidate_lines)
    system_prompt = (
        "你是高校课堂平台的 AI 博客选题主编，只输出合法 JSON。"
        "请从新闻候选中挑出最适合所有专业学生闲逛博客时阅读的前沿、有趣、有讨论价值的内容。"
        "课程关键词只代表信息检索方向，不要求文章必须点题到某门课程。"
        "避免重复、广告软文、空泛资讯、纯商业稿、标题党和不适合课堂公开讨论的内容。"
    )
    user_message = f"""
检索关键词：
{interest_keywords}

最多选择 {max_posts} 条。请输出：
{{
  "selected": [
    {{"item_id": 123, "reason": "为什么适合学生", "angle": "改写角度"}}
  ],
  "skip_reason": "如果完全不适合发布，说明原因"
}}

候选新闻：

{candidate_text}
""".strip()
    try:
        payload = await _call_ai_json(system_prompt, user_message, task_label="blog_news_select", timeout=180.0)
        selected = payload.get("selected") if isinstance(payload, dict) else []
    except Exception as exc:
        log("AI selection failed; using score fallback", error=str(exc))
        selected = [{"item_id": item["id"], "reason": "score fallback", "angle": ""} for item in limited[:max_posts]]

    selected_ids: list[int] = []
    for entry in selected if isinstance(selected, list) else []:
        item_id = _safe_int(entry.get("item_id") if isinstance(entry, dict) else entry, 0)
        if item_id and item_id not in selected_ids:
            selected_ids.append(item_id)
        if len(selected_ids) >= max_posts:
            break
    item_map = {int(item["id"]): item for item in candidates}
    return [item_map[item_id] for item_id in selected_ids if item_id in item_map]


async def _rewrite_candidates_with_ai(
    config: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    log,
) -> list[dict[str, Any]]:
    item_blocks = []
    for item in selected_candidates:
        media = item.get("media") or []
        media_lines = []
        for index, media_item in enumerate(media[:4], start=1):
            media_lines.append(
                f"- media_{index}: {media_item.get('type') or 'link'} {media_item.get('url')} "
                f"{media_item.get('caption') or ''}"
            )
        item_blocks.append(
            "\n".join(
                [
                    f"ID: {item['id']}",
                    f"关键词: {item.get('keyword')}",
                    f"标题: {item.get('title')}",
                    f"摘要: {_truncate(item.get('summary'), MAX_AI_TEXT_CHARS)}",
                    f"发布时间: {_format_date_for_humans(item.get('published_at')) or '未知'}",
                    f"来源名称: {item.get('source_name') or _domain_from_url(item.get('canonical_url') or item.get('url'))}",
                    f"来源链接: {item.get('canonical_url') or item.get('url')}",
                    "媒体候选:",
                    "\n".join(media_lines) if media_lines else "- 无可用媒体",
                ]
            )
        )
    item_text = "\n\n---\n\n".join(item_blocks)
    system_prompt = (
        "你是 Lanshare 博客中心里一位会写东西的真人感作者：有老师的判断力、极客的好奇心、同学间唠嗑的松弛感。"
        "只输出合法 JSON，不输出推理过程。"
        "写作必须是简体中文，口语自然，像从某个网站刷到一件新鲜事后顺手和同学们聊两句。"
        "可以用“我刚从某某看到”“不知道你们最近有没有注意到”“这个事有点意思”这类开头，但不要每篇都套同一个模板。"
        "语气要像真人、极客、老师、略懂一点的同学混在一起：懂一点门道，但不端着；幽默、有梗但不油腻。"
        "准确克制，不复制原文句子，不编造新闻没有的信息。"
        "如果有配图，请在正文自然位置放入 {{image_1}} 这类占位符；不要使用外链图片。"
        "不要自行添加参考来源、参考文献、课程关联、课后思考、评论引导或结尾点题，系统会在末尾统一追加正式引用。"
    )
    user_message = f"""
请为以下 {len(selected_candidates)} 条新闻分别生成博客帖子。

输出格式：
{{
  "posts": [
    {{
      "source_item_ids": [123],
      "title": "自然、不标题党的博客标题",
      "content_md": "Markdown 正文，可包含 {{{{image_1}}}} 占位符",
      "tags": ["极客闲聊", "今日科技"]
    }}
  ]
}}

写作约束：
- 面向所有专业学生，像一个真实的人在博客中心和大家随手聊科技新闻。
- 不要强行关联任何课程，不要写“这和某某课有关”“同学们可以思考”这类课堂收束。
- 不要结尾点题，不要最后再抛问题引导评论；有想法就在正文里自然聊掉。
- 开头要像唠嗑，例如“我刚从某某看到一件事……”“不知道你们最近有没有关注……”，但要根据来源和内容变化表达。
- 正文可以随性，但逻辑要清楚：先把事说明白，再聊它哪里有趣、可能影响什么、值得留意什么。
- 不要泄露任何后台、侧写、筛选逻辑或 AI 提示词。
- 不要照搬新闻原文，引用只用链接标题。
- 视频、报告、代码仓库等非图片媒体请作为普通链接处理。
- 每篇控制在 450-850 字，段落短一点，少用小标题，尽量像聊天。
- 不要在正文末尾输出“参考来源”“引用”“来源链接”等列表，系统会统一追加。

新闻材料：

{item_text}
""".strip()
    payload = await _call_ai_json(system_prompt, user_message, task_label="blog_news_rewrite", timeout=240.0)
    posts = payload.get("posts") if isinstance(payload, dict) else []
    if not isinstance(posts, list):
        log("AI rewrite returned no posts")
        return []
    return [post for post in posts if isinstance(post, dict)]


async def _call_ai_json(system_prompt: str, user_message: str, *, task_label: str, timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=AI_ASSISTANT_URL, timeout=timeout) as client:
        response = await client.post(
            "/api/ai/chat",
            json={
                "system_prompt": system_prompt,
                "messages": [],
                "new_message": user_message,
                "model_capability": "thinking",
                "task_type": "deep_text_reasoning",
                "response_format": "json",
                "task_priority": "background",
                "task_label": task_label,
                "web_search_enabled": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"AI returned failure: {data}")
        response_json = data.get("response_json")
        if not isinstance(response_json, dict):
            raise RuntimeError("AI did not return a JSON object")
        return response_json


async def _publish_rewritten_posts(
    config: dict[str, Any],
    post_payloads: list[dict[str, Any]],
    selected_candidates: list[dict[str, Any]],
    run_id: int,
    log,
) -> tuple[int, int]:
    item_map = {int(item["id"]): item for item in selected_candidates}
    published_count = 0
    skipped_count = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": config.get("user_agent") or DEFAULT_USER_AGENT},
        timeout=float(config.get("request_timeout_seconds") or 12.0),
        follow_redirects=True,
    ) as client:
        for payload in post_payloads[: int(config.get("max_posts_per_run") or 2)]:
            source_ids = [_safe_int(item, 0) for item in (payload.get("source_item_ids") or [])]
            source_ids = [item_id for item_id in source_ids if item_id in item_map]
            if not source_ids:
                skipped_count += 1
                continue
            primary = item_map[source_ids[0]]
            title = _truncate(payload.get("title") or primary.get("title") or "", 180)
            content_md = str(payload.get("content_md") or "").strip()
            if not title or not content_md:
                skipped_count += 1
                continue
            media_slots = []
            if config.get("fetch_images") and int(config.get("max_images_per_post") or 0) > 0:
                media_slots = await _build_local_image_slots(primary, config, client)
            with get_db_connection() as conn:
                registered_slots = _register_image_slots(conn, media_slots)
                final_content = _finalize_post_markdown(content_md, registered_slots, [item_map[item_id] for item_id in source_ids])
                tags = _normalize_post_tags(payload.get("tags"), primary)
                status = POST_STATUS_PUBLISHED if config.get("auto_publish") else POST_STATUS_DRAFT
                post = create_post(
                    conn,
                    ASSISTANT_USER,
                    title=title,
                    content_md=final_content,
                    author_display_mode=AUTHOR_DISPLAY_REAL,
                    visibility=VISIBILITY_PUBLIC,
                    allow_comments=True,
                    tags=tags,
                    status=status,
                )
                post_id = int(post["id"])
                if config.get("featured_posts") and status == POST_STATUS_PUBLISHED:
                    now = _now_iso()
                    conn.execute(
                        """
                        UPDATE blog_posts
                        SET is_featured = 1, featured_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, post_id),
                    )
                placeholders = ",".join("?" for _ in source_ids)
                conn.execute(
                    f"""
                    UPDATE blog_news_crawler_items
                    SET selected = 1, post_id = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (post_id, _now_iso(), *source_ids),
                )
                conn.commit()
            published_count += 1
            log("published curated blog post", post_id=post_id, title=title)
    return published_count, skipped_count


def _normalize_post_tags(tags: Any, primary: dict[str, Any]) -> list[str]:
    normalized = _split_keywords(tags if isinstance(tags, list) else str(tags or ""))
    for tag in ["极客闲聊", "今日科技", str(primary.get("keyword") or "")]:
        if tag and tag.lower() not in {item.lower() for item in normalized}:
            normalized.append(tag)
    return normalized[:5]


async def _build_local_image_slots(
    candidate: dict[str, Any],
    config: dict[str, Any],
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    media = candidate.get("media") or []
    image_urls: list[str] = []
    for item in media:
        url = str(item.get("url") or "")
        media_type = str(item.get("type") or "").lower()
        mime_type = str(item.get("mime_type") or "").lower()
        if media_type == "image" or "image" in mime_type or _looks_like_image_url(url):
            if url not in image_urls:
                image_urls.append(url)
        if len(image_urls) >= int(config.get("max_images_per_post") or 1):
            break

    slots: list[dict[str, Any]] = []
    for index, url in enumerate(image_urls, start=1):
        try:
            stored = await _download_and_store_image(
                client,
                url,
                max_bytes=int(config.get("max_image_bytes") or 6 * 1024 * 1024),
            )
        except Exception as exc:
            print(f"[BLOG_NEWS] image download skipped {url}: {exc}")
            continue
        stored.update(
            {
                "token": f"{{{{image_{index}}}}}",
                "source_url": url,
                "caption": f"{candidate.get('title') or '新闻配图'} 配图",
            }
        )
        slots.append(stored)
    return slots


async def _download_and_store_image(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> dict[str, Any]:
    canonical_url = _canonicalize_url(url)
    if not canonical_url:
        raise ValueError("invalid image URL")
    async with client.stream("GET", canonical_url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in IMAGE_MIME_EXTENSIONS and not _looks_like_image_url(canonical_url):
            raise ValueError(f"unsupported image type: {content_type}")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError("image too large")
    image_bytes = bytes(data)
    if not image_bytes:
        raise ValueError("empty image")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            detected_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("invalid image bytes") from exc
    if content_type not in IMAGE_MIME_EXTENSIONS:
        content_type = {
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(detected_format, "image/jpeg")
    file_hash = hashlib.sha256(image_bytes).hexdigest()
    target_path = global_file_write_path(file_hash)
    if resolve_global_file_path(file_hash) is None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(image_bytes)
    extension = IMAGE_MIME_EXTENSIONS.get(content_type, ".jpg")
    return {
        "file_hash": file_hash,
        "filename": f"ai-news-{file_hash[:12]}{extension}",
        "mime_type": content_type,
        "file_size": len(image_bytes),
        "image_width": int(width or 0),
        "image_height": int(height or 0),
    }


def _register_image_slots(conn, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registered: list[dict[str, Any]] = []
    for slot in slots:
        asset = register_media_asset(
            conn,
            ASSISTANT_USER,
            file_hash=slot["file_hash"],
            filename=slot["filename"],
            mime_type=slot["mime_type"],
            file_size=int(slot["file_size"] or 0),
            image_width=int(slot.get("image_width") or 0),
            image_height=int(slot.get("image_height") or 0),
        )
        token = str(slot.get("token") or "")
        registered.append(
            {
                "token": token,
                "markdown": (
                    f"![{slot.get('caption') or asset['original_filename']}]"
                    f"(/api/blog/image/{asset['file_hash']})\n\n"
                    f"> 配图来源：[{_domain_from_url(slot.get('source_url') or '')}]({slot.get('source_url')})"
                ),
            }
        )
    return registered


def _strip_ai_generated_tail(content: str) -> str:
    text = str(content or "").strip()
    patterns = [
        r"\n-{3,}\s*\n\s*(?:#{1,6}\s*)?(参考来源|参考文献|引用|来源链接|资料来源)[:：]?\s*[\s\S]*$",
        r"\n\s*(?:#{1,6}\s*)?(参考来源|参考文献|引用|来源链接|资料来源)[:：]?\s*[\s\S]*$",
        r"\n\s*(?:#{1,6}\s*)?(课后思考|小问题|评论区|最后想说|总之)[:：]?\s*[\s\S]{0,260}$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    return text


def _format_reference_date(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "n.d."
    return parsed.strftime("%Y-%m-%d")


def _format_source_references(source_items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen_urls: set[str] = set()
    for item in source_items:
        url = str(item.get("canonical_url") or item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        index = len(lines) + 1
        title = _truncate(item.get("title") or "Untitled", 120)
        source_name = _truncate(item.get("source_name") or _domain_from_url(url) or "Online source", 80)
        published_date = _format_reference_date(item.get("published_at"))
        lines.append(f"> [{index}] {source_name}. ({published_date}). {title}. Retrieved from [{url}]({url})")
    if not lines:
        return []
    return ["> 参考文献"] + lines


def _finalize_post_markdown(content_md: str, image_slots: list[dict[str, str]], source_items: list[dict[str, Any]]) -> str:
    content = _strip_ai_generated_tail(content_md)
    used_image = False
    for slot in image_slots:
        token = slot.get("token") or ""
        markdown = slot.get("markdown") or ""
        if token and token in content:
            content = content.replace(token, markdown, 1)
            used_image = True
    content = MARKDOWN_IMAGE_TOKEN_PATTERN.sub("", content)
    if image_slots and not used_image:
        content = _inject_after_first_paragraph(content, image_slots[0]["markdown"])

    source_lines = _format_source_references(source_items)
    if source_lines:
        content = f"{content}\n\n" + "\n".join(source_lines)
    return content.strip()


def _inject_after_first_paragraph(content: str, insertion: str) -> str:
    parts = str(content or "").split("\n\n", 1)
    if len(parts) == 1:
        return f"{content}\n\n{insertion}".strip()
    return f"{parts[0].strip()}\n\n{insertion}\n\n{parts[1].strip()}".strip()


def _default_worker_id() -> str:
    return os.getenv("BLOG_NEWS_CRAWLER_WORKER_ID") or f"blog-crawler-{socket.gethostname()}"
