"""LessonDoc 壳 HTML 生成与反抽取(配置↔文档可逆的两端).

- render_lesson_html / render_home_html: deck/manifest → 壳 HTML(内嵌 JSON,
  引用包内 ../assets/ 引擎;渲染本身发生在浏览器端 deck-engine.js)。
- extract_embedded_json: 壳 HTML → 原始 JSON(无损反抽取)。
- extract_deck_text: deck → 纯文本(供 AI 知识注入/搜索摘要)。

纯字符串操作,不触库、不落盘;确定性输出(同输入同输出)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import spec
from .validate_html import sanitize_html_body
from lxml import html as lxml_html

_DATA_SCRIPT_RE = re.compile(
    r"<script[^>]*id=[\"']" + re.escape(spec.DATA_SCRIPT_ID) + r"[\"'][^>]*>([\s\S]*?)</script>",
    re.IGNORECASE,
)
_MARKER_RE = re.compile(re.escape(spec.HTML_MARKER_ATTR) + r"\s*=\s*[\"']([^\"']+)[\"']")


def _esc_attr(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _embed_json(payload: dict[str, Any]) -> str:
    """内嵌 JSON;把 ``</`` 转义成 ``<\\/`` 防止提前闭合 script 标签."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return text.replace("</", "<\\/")


def _shell(
    *,
    kind: str,
    title: str,
    payload: dict[str, Any],
    asset_prefix: str,
    lesson_no: int | None = None,
    include_slides_assets: bool,
    base_href: str | None = None,
    asset_version: str = "",
) -> str:
    def asset_url(name):
        return _esc_attr(asset_prefix + name + ("?v=" + asset_version if asset_version else ""))
    head_links = [
        f'<link rel="stylesheet" href="{asset_url("course.css")}">',
    ]
    if include_slides_assets:
        head_links.append(f'<link rel="stylesheet" href="{asset_url("slides.css")}">')
    head_links.append(f'<link rel="stylesheet" href="{asset_url("themes.css")}">')

    scripts = [f'<script src="{asset_url("course.js")}"></script>']
    if include_slides_assets:
        scripts.append(f'<script src="{asset_url("slides.js")}"></script>')
    scripts.append(f'<script src="{asset_url("deck-engine.js")}"></script>')
    # 2.1 行为运行时(动作/codewalk/编辑桥接);旧壳页缺此行时由 deck-engine 自动注入
    scripts.append(f'<script src="{asset_url("interact.js")}"></script>')

    lesson_attr = f' data-lesson="{int(lesson_no)}"' if lesson_no else ""
    body_class = ' class="slides-page"' if include_slides_assets else ""
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="zh-CN" {spec.HTML_MARKER_ATTR}="2.0" data-doc-kind="{kind}"{lesson_attr}>\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{_esc_attr(title)}</title>\n"
        + (f'  <base href="{_esc_attr(base_href)}">\n' if base_href else "")
        + "".join(f"  {link}\n" for link in head_links)
        + "</head>\n"
        f"<body{body_class}>\n"
        "  <noscript>本文档需要启用 JavaScript 才能查看。</noscript>\n"
        f'  <script type="application/json" id="{spec.DATA_SCRIPT_ID}">\n'
        f"{_embed_json(payload)}\n"
        "  </script>\n"
        + "".join(f"  {s}\n" for s in scripts)
        + "</body>\n</html>\n"
    )


def render_lesson_html(deck: dict[str, Any]) -> str:
    """课次 deck(应已过 validate_deck)→ lesson_N.html 全文."""
    lesson_no = int(deck.get("lesson") or 0)
    title = f"第{lesson_no}课 · {deck.get('title') or ''}"
    return _shell(
        kind=spec.DOC_KIND_LESSON,
        title=title,
        payload=deck,
        asset_prefix="../assets/",
        lesson_no=lesson_no,
        include_slides_assets=True,
    )


def render_home_html(manifest: dict[str, Any]) -> str:
    """course.json(应已过 validate_manifest)→ main.html 全文."""
    course = manifest.get("course") or {}
    title = f"{course.get('name') or '课程'} · 课程学习文档"
    return _shell(
        kind=spec.DOC_KIND_HOME,
        title=title,
        payload=manifest,
        asset_prefix="assets/",
        include_slides_assets=False,
    )


def render_editor_preview(document, *, root_material_id, lesson_no, asset_version):
    """Platform scripts only; package-relative media still uses the authorized route."""
    prefix = f"/materials/render/{int(root_material_id)}/"
    if lesson_no:
        prefix += f"lesson_{int(lesson_no)}/"
    return _shell(kind=spec.DOC_KIND_LESSON if lesson_no else spec.DOC_KIND_HOME,
                  title=document.get("title") or (document.get("course") or {}).get("name") or "文档预览",
                  payload=document, asset_prefix="/static/lessondoc/2.0/", lesson_no=lesson_no or None,
                  include_slides_assets=bool(lesson_no), base_href=prefix, asset_version=asset_version)


def is_lessondoc_html(html_text: str) -> bool:
    """判断一段 HTML 是否为 LessonDoc 壳(带 data-lessondoc 标志)."""
    if not html_text:
        return False
    head = html_text[:2000]
    return bool(_MARKER_RE.search(head))


def extract_embedded_json(html_text: str) -> dict[str, Any] | None:
    """从壳 HTML 反抽取内嵌 JSON;失败返回 None(调用方自行降级)."""
    if not html_text:
        return None
    match = _DATA_SCRIPT_RE.search(html_text)
    if not match:
        return None
    raw = match.group(1).replace("<\\/", "</")
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------- 文本抽取

_TEXT_FIELDS = ("md", "text", "title", "sub", "subtitle", "label", "value",
                "q", "question", "explain", "summary", "nextUp", "hint",
                "caption", "code", "output", "out", "line", "mark", "note")


def _collect_text(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") == "html":
            body = sanitize_html_body(value.get("body"), [], where="text")
            if body:
                out.append(lxml_html.fragment_fromstring(body, create_parent="div").text_content().strip())
        for key in _TEXT_FIELDS:
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        for key in ("blocks", "left", "right", "items", "slides", "steps",
                    "tabs", "areas", "options", "rows", "head", "children",
                    "nodes", "edges", "actors", "messages", "layers", "links",
                    "lessons", "stages", "stage", "objects", "overlays", "globals", "lines", "sections", "home"):
            if key in value:
                _collect_text(value[key], out)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            else:
                _collect_text(item, out)


def extract_deck_text(payload: dict[str, Any], *, max_chars: int = 12000) -> str:
    """deck/manifest → 去结构纯文本(AI 知识注入用)."""
    parts: list[str] = []
    for key in ("course", "title", "subtitle", "badge"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, dict):
            _collect_text(v, parts)
    _collect_text(payload.get("slides"), parts)
    _collect_text(payload.get("lessons"), parts)
    _collect_text(payload.get("stages"), parts)
    _collect_text(payload.get("tabs"), parts)
    _collect_text(payload.get("globals"), parts)
    _collect_text(payload.get("home"), parts)
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    text = "\n".join(unique)
    return text[:max_chars]
