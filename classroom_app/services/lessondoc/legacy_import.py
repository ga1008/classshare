"""旧手写 HTML 包 → LessonDoc deck JSON 的尽力抽取（允许有损，逐项告警）。

适用对象：符合《学习文档HTML包设计规范》手写的包（cnet-course 等），即
`<section class="slide">` + course.css/slides.css 那套 class 约定，但没有
`data-lessondoc` 标志、正文直接写在 HTML 里。

设计口径（与 validate.py 一致）：
- **绝不修改原包**。调用方负责把抽取结果落到新包，原文件纹丝不动。
- 有损是允许的：识别不了的节点降级为 `text` 块并记 warning，让教师知道
  哪几页需要人工回看，而不是整包拒绝迁移。
- 手写 `<svg>` 原样保留进 `svg` 块（颜色由 validate 阶段统一收敛为语义色）。

只依赖 lxml（已在 requirements.lock.txt），不引入新依赖。
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html as lxml_html

# 与 slides.css 版式类的映射
_LAYOUT_BY_CLASS = (
    ("slide--title", "title"),
    ("slide--section", "section"),
    ("slide--two-col", "two-col"),
    ("slide--center", "center"),
    ("slide--end", "end"),
    ("slide--grid", "grid"),
)
_MAX_TEXT_FALLBACK_CHARS = 240


def _classes(node) -> set[str]:
    return set(str(node.get("class") or "").split())


def _text(node) -> str:
    return " ".join((node.text_content() or "").split())


def _inner_html(node) -> str:
    parts = [node.text or ""]
    for child in node:
        parts.append(lxml_html.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < 300:
        warnings.append(message)


# ---------------------------------------------------------------- 块抽取

def _extract_cards(node) -> dict[str, Any] | None:
    cards = [c for c in node if "s-card" in _classes(c)]
    if not cards:
        return None
    cols = 2
    for cls in _classes(node):
        m = re.fullmatch(r"grid-([1-4])", cls)
        if m:
            cols = int(m.group(1))
    items = []
    for card in cards:
        head = card.find("h4")
        body = card.find("p")
        item: dict[str, Any] = {
            "title": _text(head) if head is not None else "",
            "text": _text(body) if body is not None else "",
        }
        if "big-num-card" in _classes(card):
            strong = card.find("b")
            if strong is not None:
                item = {"title": _text(strong), "text": item["text"]}
        if "fragment" in _classes(card):
            item["step"] = 1
        items.append(item)
    return {"type": "cards", "cols": cols, "items": items} if items else None


def _extract_timeline(node) -> dict[str, Any] | None:
    items = []
    for item in node:
        if "tl-item" not in _classes(item):
            continue
        b = item.find("b")
        span = item.find("span")
        entry: dict[str, Any] = {
            "title": _text(b) if b is not None else "",
            "text": _text(span) if span is not None else "",
        }
        if "fragment" in _classes(item):
            entry["step"] = 1
        items.append(entry)
    return {"type": "timeline", "items": items} if items else None


def _extract_table(node) -> dict[str, Any] | None:
    head: list[str] = []
    rows: list[list[str]] = []
    row_step = False
    for tr in node.iter("tr"):
        cells = [c for c in tr if c.tag in ("th", "td")]
        if not cells:
            continue
        values = [_text(c) for c in cells]
        if all(c.tag == "th" for c in cells):
            head = values
        else:
            rows.append(values)
            if "fragment" in _classes(tr):
                row_step = True
    if not rows:
        return None
    block: dict[str, Any] = {"type": "table", "rows": rows}
    if head:
        block["head"] = head
    if row_step:
        block["rowStep"] = True
    return block


def _extract_quiz(node) -> dict[str, Any] | None:
    q_el = node.find_class("quiz-q")
    opts = [b for b in node.iter("button") if b.get("data-k")]
    if not q_el or len(opts) < 2:
        return None
    exp_el = node.find_class("quiz-exp")
    options = []
    for btn in opts:
        text = _text(btn)
        key = str(btn.get("data-k") or "")
        # 按钮文本通常是 "A. 选项内容"，去掉前缀避免渲染时重复
        text = re.sub(rf"^{re.escape(key)}\s*[.、,．]\s*", "", text)
        options.append({"k": key, "text": text})
    explain = _text(exp_el[0]) if exp_el else ""
    explain = re.sub(r"^[✔✓]\s*", "", explain)
    return {
        "type": "quiz",
        "q": _text(q_el[0]),
        "options": options,
        "answer": str(node.get("data-answer") or options[0]["k"]),
        "explain": explain,
    }


def _extract_tasklist(node) -> dict[str, Any] | None:
    items = []
    for li in node.iter("li"):
        text = _text(li)
        if text:
            items.append(text)
    return {"type": "tasklist", "items": items} if items else None


def _extract_code(node, next_sibling) -> dict[str, Any] | None:
    pre = node.find("pre")
    if pre is None:
        return None
    block: dict[str, Any] = {"type": "code", "code": (pre.text_content() or "").strip("\n")}
    if next_sibling is not None and "code-out" in _classes(next_sibling):
        block["output"] = (next_sibling.text_content() or "").strip("\n")
    return block


def _view_box_of(svg) -> str:
    """lxml 的 HTML 解析器会把属性名小写化，viewBox 要按两种拼写取。"""
    for key in ("viewBox", "viewbox"):
        value = svg.get(key)
        if value:
            return str(value)
    return "0 0 640 300"


def _find_svg(node):
    for child in node.iter():
        if isinstance(child.tag, str) and child.tag.split("}")[-1] == "svg":
            return child
    return None


def _extract_figure(node, warnings: list[str], *, where: str) -> dict[str, Any] | None:
    svg = _find_svg(node)
    caption_el = node.find("figcaption")
    caption = _text(caption_el) if caption_el is not None else ""
    if svg is None:
        img = node.find(".//img")
        if img is not None:
            src = str(img.get("src") or "")
            _warn(warnings, f"{where}: 位图 {src} 需要随包一起迁移，请确认路径有效")
            return {"type": "media", "kind": "image", "src": src, "caption": caption}
        return None
    view_box = _view_box_of(svg)
    body = _inner_html(svg)
    block: dict[str, Any] = {"type": "svg", "viewBox": view_box, "body": body}
    if caption:
        block["caption"] = caption
    return block


def _extract_stepper_stage(node, warnings: list[str], *, where: str) -> dict[str, Any] | None:
    """步骤演示的解说词写在页面内联 JS 里，静态 HTML 抽不到 → 只保留舞台图。"""
    _warn(
        warnings,
        f"{where}: 步骤演示的解说词与动画写在页面内联脚本里，迁移后只保留了静态舞台图。"
        "补全方法：打开该页点工具条「✏ 改这一页」，要求 AI「把这张静态图改成分步演示(stepper)」即可",
    )
    stage_el = node.find_class("stage")
    target = stage_el[0] if stage_el else node
    figure = _extract_figure(target, warnings, where=where)
    if figure is not None:
        return figure
    svg = _find_svg(target)
    if svg is None:
        return None
    return {
        "type": "svg",
        "viewBox": _view_box_of(svg),
        "body": _inner_html(svg),
    }


def _extract_blocks(container, warnings: list[str], *, where: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    children = list(container)
    for index, node in enumerate(children):
        if not isinstance(node.tag, str):
            continue
        cls = _classes(node)
        tag = node.tag.lower()
        next_sibling = children[index + 1] if index + 1 < len(children) else None
        block: dict[str, Any] | None = None

        if "code-out" in cls:
            continue  # 已被前一个 code-block 吸收
        if "code-block" in cls:
            block = _extract_code(node, next_sibling)
        elif "quiz" in cls:
            block = _extract_quiz(node)
        elif "stepper" in cls:
            block = _extract_stepper_stage(node, warnings, where=where)
        elif "tasklist" in cls:
            block = _extract_tasklist(node)
        elif "s-timeline" in cls:
            block = _extract_timeline(node)
        elif any(c.startswith("grid-") for c in cls):
            block = _extract_cards(node)
        elif "callout" in cls:
            tone = "think" if "think" in cls else "warn"
            block = {"type": "callout", "tone": tone, "md": _text(node)}
        elif "mindmap" in cls:
            _warn(warnings, f"{where}: 思维导图已按嵌套列表抽取为文本，建议改用 diagram/mindmap 结构")
            block = {"type": "text", "md": _text(node)[:_MAX_TEXT_FALLBACK_CHARS]}
        elif tag == "figure":
            block = _extract_figure(node, warnings, where=where)
        elif tag == "table" or "nice" in cls:
            block = _extract_table(node)
        elif tag in ("ul", "ol"):
            items = [_text(li) for li in node.iter("li") if _text(li)]
            if items:
                block = {"type": "cards", "cols": 1, "items": [{"title": "", "text": i} for i in items]}
        elif tag in ("h3", "h4"):
            text = _text(node)
            if text:
                block = {"type": "text", "md": f"**{text}**"}
        elif tag in ("p", "div", "span", "small"):
            text = _text(node)
            if text:
                block = {"type": "text", "md": text[:_MAX_TEXT_FALLBACK_CHARS]}

        if block is None:
            text = _text(node)
            if text:
                _warn(warnings, f"{where}: 未识别的结构 <{tag}> 已降级为纯文本，请人工回看")
                block = {"type": "text", "md": text[:_MAX_TEXT_FALLBACK_CHARS]}
            else:
                continue
        if "fragment" in cls and "step" not in block:
            block["step"] = 1
        blocks.append(block)
    return blocks


# ---------------------------------------------------------------- 页与文档

def _slide_layout(section) -> str:
    cls = _classes(section)
    for marker, layout in _LAYOUT_BY_CLASS:
        if marker in cls:
            return layout
    return "content"


def _extract_slide(section, warnings: list[str], *, index: int) -> dict[str, Any] | None:
    where = f"第 {index + 1} 页"
    layout = _slide_layout(section)
    slide: dict[str, Any] = {"layout": layout}
    data_section = section.get("data-section")
    if data_section:
        slide["section"] = data_section

    if layout == "title":
        badge = section.find_class("lesson-badge")
        h1 = section.find("h1")
        sub = section.find_class("title-sub")
        if badge:
            slide["badge"] = _text(badge[0])
        if h1 is not None:
            slide["title"] = _text(h1)
        if sub:
            slide["sub"] = _text(sub[0])
        return slide

    if layout == "section":
        no = section.find_class("sec-no")
        title = section.find_class("sec-title")
        hint = section.find_class("sec-hint")
        slide["no"] = _text(no[0]) if no else ""
        slide["title"] = _text(title[0]) if title else ""
        if hint:
            slide["hint"] = _text(hint[0])
        return slide

    title_el = section.find_class("slide-title")
    if title_el:
        slide["title"] = _text(title_el[0])
    sub_el = section.find_class("slide-sub")
    if sub_el:
        slide["sub"] = _text(sub_el[0])

    body_el = section.find_class("slide-body")
    container = body_el[0] if body_el else section

    if layout == "end":
        h2 = section.find("h2")
        if h2 is not None:
            slide["title"] = _text(h2)
        next_up = section.find_class("next-up")
        if next_up:
            slide["nextUp"] = _text(next_up[0])
        paragraphs = [_text(p) for p in section.iter("p") if _text(p)]
        summary = next((p for p in paragraphs if p and "返回" not in p), "")
        if summary:
            slide["summary"] = summary
        return slide

    if layout == "two-col":
        cols = [c for c in container if isinstance(c.tag, str)]
        if len(cols) >= 2:
            slide["left"] = _extract_blocks(cols[0], warnings, where=f"{where} 左栏")
            slide["right"] = _extract_blocks(cols[1], warnings, where=f"{where} 右栏")
            if slide["left"] or slide["right"]:
                return slide
        slide["layout"] = "content"
        layout = "content"

    blocks = _extract_blocks(container, warnings, where=where)
    if not blocks:
        _warn(warnings, f"{where}: 没有抽到任何内容块，已跳过该页")
        return None
    slide["blocks"] = blocks
    return slide


def _apply_step_order(slide: dict[str, Any]) -> None:
    """把「是 fragment」的标记换算成页内递增的登场序号。"""
    counter = 0

    def walk(blocks: list[dict[str, Any]]) -> None:
        nonlocal counter
        for block in blocks:
            if block.get("step") == 1:
                counter += 1
                block["step"] = counter
            for item in block.get("items") or []:
                if isinstance(item, dict) and item.get("step") == 1:
                    counter += 1
                    item["step"] = counter

    for key in ("blocks", "left", "right"):
        if isinstance(slide.get(key), list):
            walk(slide[key])


def extract_deck_from_legacy_html(
    raw_html: str,
    *,
    lesson_no: int,
    course_name: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """旧课次页 HTML → deck JSON（尽力而为）。返回 (deck, warnings)。"""
    warnings: list[str] = []
    tree = lxml_html.fromstring(raw_html)
    sections = [s for s in tree.iter("section") if "slide" in _classes(s)]
    if not sections:
        raise ValueError('这个页面里找不到 <section class="slide">，不是可迁移的手写课次页')

    slides: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        try:
            slide = _extract_slide(section, warnings, index=index)
        except Exception as exc:  # pragma: no cover — 单页异常不拖垮整课
            _warn(warnings, f"第 {index + 1} 页解析失败（{exc}），已跳过")
            continue
        if slide is not None:
            _apply_step_order(slide)
            slides.append(slide)
    if not slides:
        raise ValueError("所有页面都没能抽出内容")

    deck_title = ""
    subtitle = ""
    badge = ""
    if slides and slides[0].get("layout") == "title":
        deck_title = str(slides[0].get("title") or "")
        subtitle = str(slides[0].get("sub") or "")
        badge = str(slides[0].get("badge") or "")
        slides[0] = {"layout": "title"}
    if not deck_title:
        title_el = tree.find(".//title")
        deck_title = _text(title_el) if title_el is not None else f"第{lesson_no}课"

    deck_el = tree.find_class("deck")
    course = course_name or (str(deck_el[0].get("data-course") or "") if deck_el else "")

    return (
        {
            "spec": "lessondoc/2.0",
            "kind": "lesson",
            "lesson": int(lesson_no),
            "course": course,
            "title": deck_title,
            "subtitle": subtitle,
            "badge": badge or f"第 {lesson_no} 课",
            "slides": slides,
        },
        warnings,
    )


def extract_manifest_from_legacy_home(
    raw_html: str,
    *,
    lessons: list[dict[str, Any]],
    course_name: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """旧首页 HTML → course.json（课次清单以实际抽到的课次为准）。"""
    warnings: list[str] = []
    tree = lxml_html.fromstring(raw_html)

    h1 = tree.find(".//h1")
    name = course_name or (_text(h1) if h1 is not None else "课程")
    intro = ""
    hero = tree.find_class("hero")
    if hero:
        paragraphs = [_text(p) for p in hero[0].iter("p") if _text(p)]
        intro = paragraphs[0] if paragraphs else ""

    course: dict[str, Any] = {"name": name, "intro": intro[:200]}
    for stat in tree.find_class("stat"):
        label_el = stat.find("span")
        value_el = stat.find("b")
        if label_el is None or value_el is None:
            continue
        label, value = _text(label_el), _text(value_el)
        # 学分可能写成 "4.0"，不能粗暴去小数点；其余取整数部分即可。
        number = re.search(r"\d+(?:\.\d+)?", value)
        if "学时" in label and number:
            course["totalHours"] = int(float(number.group()))
        # 手写包里「课次」也常写成「次课」（cnet-course 即为「次课(16周)」）。
        elif ("课次" in label or "次课" in label) and number:
            course["sessionCount"] = int(float(number.group()))
        elif "学分" in label and number:
            credits = float(number.group())
            course["credits"] = int(credits) if credits.is_integer() else credits
        elif "考核" in label:
            course["assessment"] = value

    if not lessons:
        raise ValueError("没有可迁移的课次，无法生成课程清单")
    stages = [{"label": "全部课次", "lessons": [int(item["n"]) for item in lessons]}]
    _warn(warnings, "首页的阶段分组无法从 HTML 可靠还原，已合并为「全部课次」，请在向导里重新分组")

    return (
        {
            "spec": "lessondoc/2.0",
            "kind": "home",
            "course": course,
            "stages": stages,
            "lessons": lessons,
            "conventions": {"submit": "作业/实验报告一律在 lanshare 平台完成提交"},
        },
        warnings,
    )
