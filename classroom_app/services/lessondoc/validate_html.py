"""LessonDoc 2.1 `html` 块消毒器(设计: docs/lessondoc-editor-2026-09.md §4.8).

- body:lxml 解析 → 标签白名单(危险标签整棵删除,其余未知标签剥壳留子节点)
  → 属性白名单 → style 属性值过滤 → src/href 只许包内相对路径或 #锚点。
- css:按语法树过滤属性和函数,JSON 保存局部规则;渲染时再限定块作用域。
- svg:解析后应用元素/属性白名单,只允许包内片段的绘制引用。
前端引擎使用 DOM/CSSOM 再校验;服务端仍是保存时的安全边界。
"""

from __future__ import annotations

from html import escape
from typing import Any

from lxml import etree
from lxml import html as lxml_html

from . import spec
from .css_policy import clean_declarations, clean_rules
from .paths import local_src_ok

_DANGEROUS_TAGS = frozenset(
    {"script", "style", "iframe", "object", "embed", "link", "meta", "form", "input",
     "button", "textarea", "select", "video", "audio", "source", "base", "foreignobject",
     "template", "noscript", "frame", "frameset", "applet"}
)
SVG_TAGS = frozenset("svg g a path rect circle ellipse line polyline polygon text tspan defs marker lineargradient radialgradient stop title desc clippath mask pattern use".split())
_SVG_CASE = {name.lower(): name for name in ("viewBox", "markerWidth", "markerHeight", "refX", "refY", "linearGradient", "radialGradient", "clipPath", "gradientUnits", "gradientTransform", "preserveAspectRatio", "patternUnits")}
_PAINT_ATTRS = frozenset({"fill", "stroke", "marker-end", "marker-start", "marker-mid", "clip-path", "mask", "filter"})


def _safe_paint(value: str) -> bool:
    import re
    # Local SVG paint-server references are the only URL allowed here.
    if re.fullmatch(r"url\(\s*#[A-Za-z0-9_-]+\s*\)", value):
        return True
    return not any(c in value for c in "\\<>") and "url" not in value.lower() and "expression" not in value.lower()


def sanitize_svg_markup(text: str, warnings: list[str], *, where: str) -> str:
    try:
        root = lxml_html.fragment_fromstring(text, create_parent="div")
    except (etree.ParserError, ValueError):
        _warn(warnings, f"{where}: svg 无法解析,已丢弃")
        return ""
    removed = False
    for node in list(root.iterdescendants()):
        tag = _localname(node.tag)
        if tag not in SVG_TAGS:
            removed = True
            if isinstance(node.tag, str):
                node.drop_tree()
            elif node.getparent() is not None:
                node.getparent().remove(node)
            continue
        node.tag = _SVG_CASE.get(tag, tag)
        for attr, value in list(node.attrib.items()):
            key = attr.lower()
            allowed = key in spec.HTML_ALLOWED_ATTRS or key in {"gradientunits", "gradienttransform", "preserveaspectratio", "patternunits", "clip-path"}
            if not allowed or (key in {"href", "src"} and not (value.startswith("#") and local_src_ok(value, anchor=True))) or (key in _PAINT_ATTRS and not _safe_paint(value)):
                del node.attrib[attr]
                removed = True
            elif key == "style":
                clean, bad = clean_declarations(value)
                removed |= bad
                if clean:
                    node.set(attr, clean)
                else:
                    del node.attrib[attr]
            elif key in _SVG_CASE:
                del node.attrib[attr]
                node.set(_SVG_CASE[key], value)
    if removed:
        _warn(warnings, f"{where}: svg 含不允许的标签或属性,已剥除")
    return escape(root.text or "", quote=False) + "".join(etree.tostring(child, encoding="unicode", method="xml") for child in root)


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < 200:
        warnings.append(message)


def _src_ok(value: str) -> bool:
    return local_src_ok(value, anchor=True)


def _localname(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1].lower()


def sanitize_html_body(body: Any, warnings: list[str], *, where: str) -> str:
    text = str(body or "")
    if not text.strip():
        return ""
    if len(text) > spec.MAX_HTML_BODY_CHARS:
        text = text[: spec.MAX_HTML_BODY_CHARS]
        _warn(warnings, f"{where}: html 内容超长已截断")
    try:
        root = lxml_html.fragment_fromstring(text, create_parent="div")
    except (etree.ParserError, ValueError) as exc:
        _warn(warnings, f"{where}: html 无法解析({exc}),已丢弃")
        return ""

    removed_tags: set[str] = set()
    removed_attrs: set[str] = set()
    # 先收集再改树,避免遍历中修改
    for el in list(root.iter()):
        if el is root:
            continue
        if not isinstance(el.tag, str):        # 注释/处理指令
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
            continue
        name = _localname(el.tag)
        if name in _DANGEROUS_TAGS:
            removed_tags.add(name)
            el.drop_tree()
            continue
        if name not in spec.HTML_ALLOWED_TAGS:
            removed_tags.add(name)
            el.drop_tag()                      # 剥壳留子节点
            continue
        for attr in list(el.attrib.keys()):
            key = attr.split("}", 1)[-1].lower()
            value = el.attrib[attr]
            if key.startswith("on") or key not in spec.HTML_ALLOWED_ATTRS:
                removed_attrs.add(key)
                del el.attrib[attr]
                continue
            if key == "style":
                cleaned, dropped = clean_declarations(value)
                if dropped:
                    removed_attrs.add("style")
                if cleaned:
                    el.attrib[attr] = cleaned
                else:
                    del el.attrib[attr]
            elif key in {"src", "href"} and not _src_ok(value):
                removed_attrs.add(key)
                del el.attrib[attr]
            elif key in _PAINT_ATTRS and not _safe_paint(value):
                removed_attrs.add(key)
                del el.attrib[attr]
    if removed_tags:
        _warn(warnings, f"{where}: html 含不允许的标签已处理: {', '.join(sorted(removed_tags))}")
    if removed_attrs:
        _warn(warnings, f"{where}: html 含不允许的属性已剥除: {', '.join(sorted(removed_attrs))}")
    inner = escape(root.text or "", quote=False) + "".join(
        lxml_html.tostring(child, encoding="unicode", method="html") for child in root
    )
    return inner.strip()


def sanitize_html_css(css: Any, warnings: list[str], *, where: str, scope: str | None = None) -> str:
    """Canonical local CSS; scope only for compatibility/export callers."""
    text = str(css or "")
    if not text.strip():
        return ""
    if len(text) > spec.MAX_HTML_CSS_CHARS:
        text = text[: spec.MAX_HTML_CSS_CHARS]
        _warn(warnings, f"{where}: css 超长已截断")
    result, dropped, at_rules = clean_rules(text, scope=scope)
    if at_rules:
        _warn(warnings, f"{where}: css 中的 @ 规则已丢弃")
    if dropped:
        _warn(warnings, f"{where}: css 中不允许的规则或属性已丢弃")
    return result


def scope_html_css(css: Any, block_id: str, warnings: list[str], *, where: str) -> str:
    return sanitize_html_css(css, warnings, where=where, scope=f".ld-html-{block_id}")
