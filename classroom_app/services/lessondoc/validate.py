"""LessonDoc deck/manifest 降级式校验器.

口径(设计文档 §4):**丢块不丢页,丢页不丢课,永远渲出东西 + 明示告警**。

致命错(抛 LessonDocValidationError)仅 4 种:
  1. 整体不是 JSON 对象;
  2. spec 缺失或主版本不符;
  3. slides 非非空数组(仅 lesson);
  4. lesson 编号与目标课次不符(传入 expected_lesson 时)。

其余问题一律降级修复并记入 warnings:
  - 未知 block type → 替换为 callout(warn) 占位;
  - 块字段缺失/形状不对 → 丢弃该块并记告警;
  - 未知 layout → 按 content;
  - svg 含 script/事件属性 → 剥除;十六进制颜色 → 替换语义色;
  - 页数/块数/步数超限 → 截断。

所有函数纯内存操作,不触库、不落盘。
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from . import spec
from .validate_html import sanitize_html_body, sanitize_html_css, sanitize_svg_markup, _safe_paint
from .paths import local_src_ok
from .model import check_budget
from .validate_style import (
    clean_actions,
    clean_bg,
    clean_frame,
    clean_id,
    clean_style,
    clean_home_style,
    prune_dangling_actions,
)

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SVG_WRAPPER_RE = re.compile(
    r"^\s*(?P<open><\s*svg\b[^>]*>)(?P<inner>[\s\S]*?)<\s*/\s*svg\s*>\s*$", re.IGNORECASE
)
_VIEWBOX_ATTR_RE = re.compile(r"viewBox\s*=\s*[\"']([\d.\s-]+)[\"']", re.IGNORECASE)

# 十六进制色 → 语义变量。先按色相认语义色(成功/警告/错误/中性),
# 认不出的按亮度分档:深色→primary-dark、浅色→primary-soft、接近白→fill。
# 亮度分档是必要的——手写包里 #e0f2fe(浅蓝底) 与 #075985(深蓝字) 若都
# 兜底成同一个主色,浅底会盖住深字,图就废了。
_HEX_HINTS = (
    (re.compile(r"#(16a34a|0f9d58|22c55e|4caf50)\b", re.IGNORECASE), "var(--dg-ok)"),
    (re.compile(r"#(d97706|f59e0b|ff9800|ea580c|fbbf24)\b", re.IGNORECASE), "var(--dg-warn)"),
    (re.compile(r"#(dc2626|ef4444|f44336|b91c1c)\b", re.IGNORECASE), "var(--dg-err)"),
    (re.compile(r"#(64748b|6b7280|94a3b8|9e9e9e|cbd5e1)\b", re.IGNORECASE), "var(--dg-muted)"),
)


def _expand_hex(value: str) -> tuple[int, int, int] | None:
    """#abc / #aabbcc / #aabbccdd → (r, g, b);无法解析返回 None。"""
    raw = value.lstrip("#")
    if len(raw) in (3, 4):
        raw = "".join(ch * 2 for ch in raw[:3])
    elif len(raw) in (6, 8):
        raw = raw[:6]
    else:
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def _hex_to_semantic_var(match) -> str:
    value = match.group(0)
    rgb = _expand_hex(value)
    if rgb is None:
        return "var(--dg-primary)"
    r, g, b = rgb
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if luminance >= 0.96:
        return "var(--dg-fill)"          # 纯白/近白:节点底色
    if luminance >= 0.72:
        return "var(--dg-primary-soft)"  # 浅色:区域底衬(如 #e0f2fe)
    if luminance <= 0.32:
        return "var(--dg-primary-dark)"  # 深色:标题/强调文字
    return "var(--dg-primary)"


class LessonDocValidationError(ValueError):
    """deck/manifest 无法降级修复的致命错误."""


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < 200:
        warnings.append(message)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sanitize_svg_body(body: Any, warnings: list[str], *, where: str) -> str:
    """剥除 svg 逃生舱中的危险内容并把硬编码色替换为语义色."""
    text = _as_str(body)
    if len(text) > spec.MAX_SVG_BODY_CHARS:
        text = text[: spec.MAX_SVG_BODY_CHARS]
        _warn(warnings, f"{where}: svg 内容超长已截断")
    text = sanitize_svg_markup(text, warnings, where=where)
    if _HEX_COLOR_RE.search(text):
        for hint, var in _HEX_HINTS:
            text = hint.sub(var, text)
        remaining = len(_HEX_COLOR_RE.findall(text))
        text = _HEX_COLOR_RE.sub(_hex_to_semantic_var, text)
        _warn(warnings, f"{where}: svg 含硬编码颜色 {remaining} 处,已替换为语义色")
    return text


def _media_src_ok(src: str) -> bool:
    return local_src_ok(src)


def _clean_items(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in value if item is not None]


def _validate_block(block: Any, warnings: list[str], *, where: str, depth: int = 0) -> dict[str, Any] | None:
    """返回净化后的块;无法修复返回 None(调用方丢弃并已记告警)."""
    if not isinstance(block, dict):
        _warn(warnings, f"{where}: 内容块不是对象,已丢弃")
        return None
    if depth > 8:
        _warn(warnings, f"{where}: 内容嵌套过深,已丢弃")
        return None
    btype = _as_str(block.get("type")).strip()
    if btype not in spec.BLOCK_TYPES:
        _warn(warnings, f"{where}: 未知内容块类型 '{btype}',已替换为提示占位")
        return {"type": "callout", "tone": "warn", "md": f"(此处原为不受支持的内容块:{btype or '空'})"}
    out = copy.deepcopy(block)
    out["type"] = btype

    if btype == "text":
        md = _as_str(out.get("md", out.get("text")))
        if not md.strip():
            _warn(warnings, f"{where}: text 块为空,已丢弃")
            return None
        if len(md) > spec.MAX_TEXT_CHARS:
            _warn(warnings, f"{where}: text 块 {len(md)} 字超过 {spec.MAX_TEXT_CHARS},建议拆分(未截断)")
        out["md"] = md
        out.pop("text", None)
    elif btype in {"cards", "bignum", "timeline", "tasklist", "reveal"}:
        items = _clean_items(out.get("items"))
        if not items:
            _warn(warnings, f"{where}: {btype} 块无条目,已丢弃")
            return None
        out["items"] = items
    elif btype == "bigmark":
        if not _as_str(out.get("mark")) and not _as_str(out.get("line")):
            _warn(warnings, f"{where}: bigmark 块为空,已丢弃")
            return None
    elif btype == "table":
        rows = [row for row in _clean_items(out.get("rows")) if isinstance(row, list) and row]
        if not rows:
            _warn(warnings, f"{where}: table 块无数据行,已丢弃")
            return None
        if len(rows) > spec.MAX_TABLE_ROWS:
            rows = rows[: spec.MAX_TABLE_ROWS]
            _warn(warnings, f"{where}: 表格超过 {spec.MAX_TABLE_ROWS} 行已截断")
        elif len(rows) > spec.WARN_TABLE_ROWS:
            _warn(warnings, f"{where}: 表格 {len(rows)} 行偏大,建议拆页")
        out["rows"] = rows
    elif btype == "callout":
        md = _as_str(out.get("md", out.get("text")))
        if not md.strip():
            _warn(warnings, f"{where}: callout 块为空,已丢弃")
            return None
        out["md"] = md
        out.pop("text", None)
        if _as_str(out.get("tone")) not in spec.CALLOUT_TONES:
            out["tone"] = "think"
    elif btype in {"tabs", "details"}:
        if depth >= 2:
            _warn(warnings, f"{where}: {btype} 嵌套过深,已丢弃")
            return None
        if btype == "tabs":
            tabs = []
            for i, tab in enumerate(_clean_items(out.get("tabs"))):
                if not isinstance(tab, dict):
                    continue
                tab = dict(tab)
                tab["blocks"] = _validate_blocks(
                    tab.get("blocks"), warnings, where=f"{where}.tabs[{i}]", depth=depth + 1
                )
                tabs.append(tab)
            if not tabs:
                _warn(warnings, f"{where}: tabs 块无标签页,已丢弃")
                return None
            out["tabs"] = tabs
        else:
            out["blocks"] = _validate_blocks(
                out.get("blocks"), warnings, where=f"{where}.details", depth=depth + 1
            )
    elif btype == "code":
        if not _as_str(out.get("code")).strip():
            _warn(warnings, f"{where}: code 块为空,已丢弃")
            return None
    elif btype == "media":
        kind = _as_str(out.get("kind"))
        if kind not in spec.MEDIA_KINDS:
            out["kind"] = "image"
        src = _as_str(out.get("src"))
        if not _media_src_ok(src):
            _warn(warnings, f"{where}: media 路径不合规('{src[:80]}'),已丢弃(只允许包内相对路径)")
            return None
        if out.get("poster") and not _media_src_ok(_as_str(out.get("poster"))):
            out.pop("poster", None)
            _warn(warnings, f"{where}: 视频封面路径不合规,已忽略")
    elif btype == "svg":
        body = _as_str(out.get("body"))
        # AI 常把 body 写成完整 <svg ...>…</svg>;引擎会再套一层,剥壳并沿用其 viewBox
        wrapper = _SVG_WRAPPER_RE.match(body)
        if wrapper:
            if not _as_str(out.get("viewBox")).strip():
                vb = _VIEWBOX_ATTR_RE.search(wrapper.group("open") or "")
                if vb:
                    out["viewBox"] = vb.group(1)
            body = wrapper.group("inner")
            _warn(warnings, f"{where}: svg body 含外层 <svg> 壳,已剥除")
        out["body"] = sanitize_svg_body(body, warnings, where=where)
        if not out["body"].strip():
            _warn(warnings, f"{where}: svg 块为空,已丢弃")
            return None
    elif btype == "diagram":
        kind = _as_str(out.get("kind"))
        if kind not in spec.DIAGRAM_KINDS:
            _warn(warnings, f"{where}: diagram.kind '{kind}' 未知,按 flow 处理")
            out["kind"] = "flow"
            kind = "flow"
        if kind == "flow" and not _clean_items(out.get("nodes")):
            _warn(warnings, f"{where}: flow 图无节点,已丢弃")
            return None
        if kind == "sequence" and len(_clean_items(out.get("actors"))) < 2:
            _warn(warnings, f"{where}: sequence 图参与者不足,已丢弃")
            return None
        if kind == "arch" and not _clean_items(out.get("layers")):
            _warn(warnings, f"{where}: arch 图无层,已丢弃")
            return None
        if kind == "mindmap" and not _clean_items(out.get("children")):
            _warn(warnings, f"{where}: mindmap 无子节点,已丢弃")
            return None
    elif btype == "quiz":
        options = [o for o in _clean_items(out.get("options")) if isinstance(o, dict)]
        if not _as_str(out.get("q", out.get("question"))).strip() or len(options) < 2:
            _warn(warnings, f"{where}: quiz 块题干或选项缺失,已丢弃")
            return None
        out["options"] = options[: spec.MAX_QUIZ_OPTIONS]
        keys = {_as_str(o.get("k", o.get("key"))) for o in out["options"]}
        if _as_str(out.get("answer")) not in keys:
            first = _as_str(out["options"][0].get("k", out["options"][0].get("key")), "A")
            _warn(warnings, f"{where}: quiz 答案不在选项中,已改为 {first}")
            out["answer"] = first
    elif btype == "stepper":
        stage = out.get("stage")
        # AI 常漏写 stage.type——形状可辨认时推断补全,别把舞台图换成占位卡
        if isinstance(stage, dict) and not _as_str(stage.get("type")).strip():
            if stage.get("body"):
                stage = {**stage, "type": "svg"}
                _warn(warnings, f"{where}.stage: 缺 type,按形状推断为 svg")
            elif stage.get("kind") in spec.DIAGRAM_KINDS or stage.get("nodes") or stage.get("layers"):
                stage = {**stage, "type": "diagram"}
                _warn(warnings, f"{where}.stage: 缺 type,按形状推断为 diagram")
        stage_clean = (
            _validate_block(stage, warnings, where=f"{where}.stage", depth=depth + 1)
            if stage is not None
            else None
        )
        steps = [s for s in _clean_items(out.get("steps")) if isinstance(s, dict)]
        if stage_clean is None or not steps:
            _warn(warnings, f"{where}: stepper 缺舞台或步骤,已丢弃")
            return None
        if len(steps) > spec.MAX_STEPPER_STEPS:
            steps = steps[: spec.MAX_STEPPER_STEPS]
            _warn(warnings, f"{where}: stepper 步骤超过 {spec.MAX_STEPPER_STEPS} 已截断")
        out["stage"] = stage_clean
        for i, step in enumerate(steps):
            if "set" in step:
                safe_ops = []
                for op in _clean_items(step.get("set")):
                    if not isinstance(op, dict):
                        continue
                    attr = op.get("attr")
                    if attr not in {"textContent", "visibility", "opacity", "transform", "fill", "stroke", "stroke-width", "x", "y", "cx", "cy", "r", "width", "height", "d", "points"} or (attr != "textContent" and not _safe_paint(str(op.get("value") or ""))):
                        _warn(warnings, f"{where}.steps[{i}]: 步骤属性操作不安全,已丢弃")
                        continue
                    safe_ops.append(op)
                step["set"] = safe_ops
        out["steps"] = steps
    elif btype == "button":
        label = _as_str(out.get("label")).strip()
        if not label:
            _warn(warnings, f"{where}: button 缺少 label,已丢弃")
            return None
        out["label"] = label[:80]
        if _as_str(out.get("variant")) not in spec.BUTTON_VARIANTS:
            out.pop("variant", None)
        if _as_str(out.get("size")) not in spec.BUTTON_SIZES:
            out.pop("size", None)
    elif btype == "codewalk":
        lines = _clean_codewalk_lines(out.get("lines"), warnings, where=where)
        if not lines:
            _warn(warnings, f"{where}: codewalk 无有效代码行,已丢弃")
            return None
        out["lines"] = lines
        speed = _as_int(out.get("speedMs"), 900)
        out["speedMs"] = max(spec.CODEWALK_SPEED_RANGE[0], min(spec.CODEWALK_SPEED_RANGE[1], speed))
        for flag in ("loop", "autoStart", "arrow", "showOutput", "showNotes"):
            if flag in out:
                out[flag] = bool(out[flag])
    elif btype == "group":
        if depth >= spec.MAX_GROUP_DEPTH:
            _warn(warnings, f"{where}: group 嵌套过深,已丢弃")
            return None
        children: list[dict[str, Any]] = []
        raw_children = _clean_items(out.get("children"))
        if len(raw_children) > spec.MAX_POSITIONED_PER_SLIDE:
            _warn(warnings, f"{where}: 组内元素超过 {spec.MAX_POSITIONED_PER_SLIDE} 个,已截断")
        for i, child in enumerate(raw_children[:spec.MAX_POSITIONED_PER_SLIDE]):
            cleaned = _validate_block(child, warnings, where=f"{where}.children[{i}]", depth=depth + 1)
            if cleaned is None:
                continue
            if "frame" not in cleaned:
                _warn(warnings, f"{where}.children[{i}]: 组内元素缺少 frame,已丢弃")
                continue
            children.append(cleaned)
        if not children:
            _warn(warnings, f"{where}: group 无有效子元素,已丢弃")
            return None
        out["children"] = children
        natural = out.get("natural")
        if not (isinstance(natural, dict) and _as_int(natural.get("w"), 0) > 0 and _as_int(natural.get("h"), 0) > 0):
            max_x = max(c["frame"]["x"] + c["frame"]["w"] for c in children)
            max_y = max(c["frame"]["y"] + c["frame"]["h"] for c in children)
            out["natural"] = {"w": max(1, max_x), "h": max(1, max_y)}
    elif btype == "html":
        block_id = clean_id(out.get("id")) or "h" + hashlib.sha1(
            _as_str(out.get("body")).encode("utf-8")
        ).hexdigest()[:8]
        out["id"] = block_id
        body = sanitize_html_body(out.get("body"), warnings, where=where)
        if not body:
            _warn(warnings, f"{where}: html 块为空,已丢弃")
            return None
        out["body"] = body
        css = sanitize_html_css(out.get("css"), warnings, where=where)
        if css:
            out["css"] = css
        else:
            out.pop("css", None)
    _apply_common_block_fields(out, warnings, where=where)
    return out


def _apply_common_block_fields(out: dict[str, Any], warnings: list[str], *, where: str) -> None:
    """2.1 通用字段:id / name / frame / style / hidden / actions(原地净化)。"""
    if out.get("id") is not None:
        cleaned_id = clean_id(out.get("id"))
        if cleaned_id:
            out["id"] = cleaned_id
        else:
            out.pop("id", None)
    if out.get("name") is not None:
        name = _as_str(out.get("name")).strip()[:60]
        if name:
            out["name"] = name
        else:
            out.pop("name", None)
    if "frame" in out:
        frame = clean_frame(out.get("frame"), warnings, where=where)
        if frame is None:
            out.pop("frame", None)
        else:
            out["frame"] = frame
    if "style" in out:
        style = clean_style(out.get("style"), warnings, where=where)
        if style is None:
            out.pop("style", None)
        else:
            out["style"] = style
    if "natural" in out:
        natural = out.get("natural")
        if isinstance(natural, dict) and all(isinstance(natural.get(k), (int, float)) and not isinstance(natural.get(k), bool)
                                             and spec.NATURAL_SIZE_RANGE[0] <= natural[k] <= spec.NATURAL_SIZE_RANGE[1] for k in ("w", "h")):
            out["natural"] = {"w": round(natural["w"], 4), "h": round(natural["h"], 4)}
        else:
            out.pop("natural", None)
            _warn(warnings, f"{where}: natural 内部尺寸不合规,已忽略")
            if out.get("type") == "group":
                children = out.get("children") or []
                out["natural"] = {"w": max(1, max(c["frame"]["x"] + c["frame"]["w"] for c in children)),
                                  "h": max(1, max(c["frame"]["y"] + c["frame"]["h"] for c in children))}
    if "hidden" in out:
        if out.get("hidden"):
            out["hidden"] = True
        else:
            out.pop("hidden", None)
    if "actions" in out:
        actions = clean_actions(out.get("actions"), warnings, where=where)
        if actions:
            out["actions"] = actions
        else:
            out.pop("actions", None)


def _clean_codewalk_lines(value: Any, warnings: list[str], *, where: str) -> list[dict[str, Any]]:
    items = [line for line in _clean_items(value) if isinstance(line, (dict, str))]
    if len(items) > spec.MAX_CODEWALK_LINES:
        items = items[: spec.MAX_CODEWALK_LINES]
        _warn(warnings, f"{where}: codewalk 超过 {spec.MAX_CODEWALK_LINES} 行已截断")
    result: list[dict[str, Any]] = []
    source_count = 0
    for i, line in enumerate(items):
        if isinstance(line, str):
            line = {"code": line}
        clean: dict[str, Any] = {}
        code = line.get("code")
        ref = line.get("ref")
        if ref is not None and code is None:
            ref_int = _as_int(ref, -1)
            if ref_int < 0 or ref_int >= source_count:
                _warn(warnings, f"{where}.lines[{i}]: ref 指向不存在的源码行,已丢弃")
                continue
            clean["ref"] = ref_int
        else:
            code_text = _as_str(code)
            if len(code_text) > spec.MAX_CODEWALK_LINE_CHARS:
                code_text = code_text[: spec.MAX_CODEWALK_LINE_CHARS]
                _warn(warnings, f"{where}.lines[{i}]: 代码行超长已截断")
            clean["code"] = code_text
            source_count += 1
        for key in ("out", "note"):
            text = _as_str(line.get(key))
            if text:
                clean[key] = text[:500]
        result.append(clean)
    return result


def _validate_blocks(blocks: Any, warnings: list[str], *, where: str, depth: int = 0) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if blocks is None:
        return result
    if not isinstance(blocks, list):
        _warn(warnings, f"{where}: blocks 不是数组,已忽略")
        return result
    for i, block in enumerate(blocks):
        if len(result) >= spec.MAX_BLOCKS_PER_SLIDE:
            _warn(warnings, f"{where}: 内容块超过 {spec.MAX_BLOCKS_PER_SLIDE} 个已截断")
            break
        cleaned = _validate_block(block, warnings, where=f"{where}[{i}]", depth=depth)
        if cleaned is not None:
            result.append(cleaned)
    return result


def _validate_slide(slide: Any, warnings: list[str], *, index: int) -> dict[str, Any] | None:
    where = f"slides[{index}]"
    if not isinstance(slide, dict):
        _warn(warnings, f"{where}: 不是对象,已丢弃")
        return None
    out = copy.deepcopy(slide)
    # Only a genuinely empty submission may opt into the editor's blank-page mode.
    empty = out.get("empty") is True and not any(out.get(k) for k in ("blocks", "left", "right", "areas", "objects", "overlays"))
    if not empty:
        out.pop("empty", None)
    layout = _as_str(out.get("layout"), spec.DEFAULT_LAYOUT)
    if layout not in spec.SLIDE_LAYOUTS:
        _warn(warnings, f"{where}: 未知版式 '{layout}',按 content 处理")
        layout = spec.DEFAULT_LAYOUT
    out["layout"] = layout
    if out.get("id") is not None:
        sid = clean_id(out.get("id"))
        if sid:
            out["id"] = sid
        else:
            out.pop("id", None)
    if "bg" in out:
        bg = clean_bg(out.get("bg"), warnings, where=f"{where}.bg")
        if bg is None:
            out.pop("bg", None)
        else:
            out["bg"] = bg
    overlays = _validate_positioned(out.get("overlays"), warnings, where=f"{where}.overlays")
    if overlays:
        out["overlays"] = overlays
    else:
        out.pop("overlays", None)
    if layout == "canvas":
        objects = _validate_positioned(out.get("objects"), warnings, where=f"{where}.objects")
        if not objects and not overlays and not empty:
            _warn(warnings, f"{where}: 自由排版页无有效元素,已丢弃该页")
            return None
        if len(objects) + len(overlays) > spec.MAX_POSITIONED_PER_SLIDE:
            keep = max(0, spec.MAX_POSITIONED_PER_SLIDE - len(overlays))
            objects = objects[:keep]
            _warn(warnings, f"{where}: 定位元素超过 {spec.MAX_POSITIONED_PER_SLIDE} 个已截断")
        out["objects"] = objects
        return out
    if layout == "two-col":
        out["left"] = _validate_blocks(out.get("left"), warnings, where=f"{where}.left")
        out["right"] = _validate_blocks(out.get("right"), warnings, where=f"{where}.right")
        if not out["left"] and not out["right"] and not overlays and not empty:
            _warn(warnings, f"{where}: two-col 两栏皆空,已丢弃该页")
            return None
    elif layout == "grid":
        areas = []
        for i, area in enumerate(_clean_items(out.get("areas"))):
            if not isinstance(area, dict):
                continue
            area = dict(area)
            area["blocks"] = _validate_blocks(area.get("blocks"), warnings, where=f"{where}.areas[{i}]")
            if area["blocks"]:
                areas.append(area)
        if not areas and not overlays and not empty:
            _warn(warnings, f"{where}: grid 版式无有效区域,已丢弃该页")
            return None
        out["areas"] = areas
    elif layout in {"title", "section"}:
        pass  # 纯字段版式,字段缺失由引擎容错
    else:
        out["blocks"] = _validate_blocks(out.get("blocks"), warnings, where=f"{where}.blocks")
        if layout == "content" and not out["blocks"] and not overlays and not empty:
            _warn(warnings, f"{where}: 内容页无有效内容块,已丢弃该页")
            return None
    return out


def _validate_positioned(value: Any, warnings: list[str], *, where: str) -> list[dict[str, Any]]:
    """定位块列表:每块必须带合法 frame;超限截断。"""
    result: list[dict[str, Any]] = []
    if value is None:
        return result
    if not isinstance(value, list):
        _warn(warnings, f"{where}: 不是数组,已忽略")
        return result
    for i, block in enumerate(value):
        if len(result) >= spec.MAX_POSITIONED_PER_SLIDE:
            _warn(warnings, f"{where}: 定位元素超过 {spec.MAX_POSITIONED_PER_SLIDE} 个已截断")
            break
        cleaned = _validate_block(block, warnings, where=f"{where}[{i}]")
        if cleaned is None:
            continue
        if "frame" not in cleaned:
            _warn(warnings, f"{where}[{i}]: 定位元素缺少 frame,已丢弃")
            continue
        result.append(cleaned)
    return result


def _iter_blocks(container: Any):
    """深度遍历一个 slide/deck 片段内所有块(含 tabs/details/group/stepper 子块)。"""
    if isinstance(container, dict):
        if container.get("type") in spec.BLOCK_TYPES:
            yield container
        for key in ("blocks", "left", "right", "overlays", "objects", "children", "globals"):
            value = container.get(key)
            if isinstance(value, list):
                for item in value:
                    yield from _iter_blocks(item)
        for key in ("areas", "tabs", "sections"):
            value = container.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield from _iter_blocks({"blocks": item.get("blocks")})
        stage = container.get("stage")
        if isinstance(stage, dict):
            yield from _iter_blocks(stage)
    elif isinstance(container, list):
        for item in container:
            yield from _iter_blocks(item)


def _dedupe_ids_and_prune_actions(deck: dict[str, Any], warnings: list[str]) -> None:
    """全 deck 视角:slide/块 id 去重(后者重排)+ 删除指向不存在 id 的动作步。"""
    seen: set[str] = set()
    counter = 0

    def fresh(prefix: str) -> str:
        nonlocal counter
        while True:
            counter += 1
            candidate = f"{prefix}{counter}"
            if candidate not in seen:
                return candidate

    for i, slide in enumerate(deck.get("slides") or []):
        sid = slide.get("id")
        if sid:
            if sid in seen:
                slide["id"] = fresh("s_dup")
                _warn(warnings, f"slides[{i}]: 页面 id '{sid}' 重复,已重排")
            seen.add(slide["id"])
    scopes: list[tuple[str, Any]] = [("globals", {"globals": deck.get("globals")})]
    scopes.extend((f"slides[{i}]", s) for i, s in enumerate(deck.get("slides") or []))
    scopes.extend([("home", deck.get("home")), ("tabs", {"tabs": deck.get("tabs")})])
    blocks_with_actions: list[dict[str, Any]] = []
    original_ids = {}
    for scope_name, scope in scopes:
        for block in _iter_blocks(scope):
            bid = block.get("id")
            original_ids[id(block)] = bid
            if bid:
                if bid in seen:
                    block["id"] = fresh("b_dup")
                    _warn(warnings, f"{scope_name}: 元素 id '{bid}' 重复,已重排")
                seen.add(block["id"])
            if block.get("actions"):
                blocks_with_actions.append(block)
    original_targets = {id(b): [a.get("target") for a in b.get("actions") or []] for b in blocks_with_actions}
    def remap_scope(scope):
        blocks = list(_iter_blocks(scope))
        mapping = {}
        for b in blocks:
            old = original_ids.get(id(b))
            if old and old not in mapping:
                mapping[old] = b.get("id")
        for b in blocks:
            for action, target in zip(b.get("actions") or [], original_targets.get(id(b), [])):
                if target in mapping:
                    action["target"] = mapping[target]
    for _, scope in scopes:
        remap_scope(scope)
        # Nested groups own their internal references even when a copied group
        # arrived with the same preliminary IDs as another instance.
        for block in _iter_blocks(scope):
            if block.get("type") == "group":
                remap_scope(block)
    typed_ids = {b.get("id"): b.get("type") for _, scope in scopes for b in _iter_blocks(scope) if b.get("id")}
    slide_ids = {s.get("id"): i + 1 for i, s in enumerate(deck.get("slides") or []) if s.get("id")}
    for block in blocks_with_actions:
        prune_dangling_actions(block, set(typed_ids), warnings, where=f"元素 {block.get('id') or block.get('type')}")
        kept = []
        for action in block.get("actions") or []:
            kind = action.get("do")
            if kind in {"run", "reset"} and typed_ids.get(action.get("target")) != "codewalk":
                _warn(warnings, f"元素 {block.get('id')}: {kind} 目标不是代码演示,已删除该步")
                continue
            if kind == "goto":
                sid = action.get("slideId")
                if sid:
                    if sid not in slide_ids:
                        _warn(warnings, f"元素 {block.get('id')}: 跳转页面 '{sid}' 不存在,已删除该步")
                        continue
                    action["slide"] = slide_ids[sid]
                elif not (1 <= action.get("slide", 0) <= len(deck.get("slides") or [])):
                    _warn(warnings, f"元素 {block.get('id')}: 跳转页码越界,已删除该步")
                    continue
            kept.append(action)
        if kept:
            block["actions"] = kept
        else:
            block.pop("actions", None)



def _backfill_quiz_titles(slides: list[dict[str, Any]], warnings: list[str]) -> None:
    """测验页 AI 常漏 title,页面顶部就空了一块;按出现顺序补「第 N 题」."""
    n = 0
    for i, slide in enumerate(slides):
        blocks = slide.get("blocks") or []
        if not any(isinstance(b, dict) and b.get("type") == "quiz" for b in blocks):
            continue
        n += 1
        if not _as_str(slide.get("title")).strip():
            slide["title"] = f"第 {n} 题"
            _warn(warnings, f"slides[{i}]: 测验页缺标题,已补「第 {n} 题」")


def _check_spec_header(payload: Any, *, what: str) -> None:
    try:
        check_budget(payload)
    except ValueError as exc:
        raise LessonDocValidationError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise LessonDocValidationError(f"{what} 不是 JSON 对象")
    spec_value = _as_str(payload.get("spec"))
    if not spec_value.startswith(spec.SPEC_MAJOR_PREFIX):
        raise LessonDocValidationError(
            f"{what} 的 spec 版本不受支持: '{spec_value or '缺失'}'(需要 {spec.SPEC_MAJOR_PREFIX}.x)"
        )


def validate_deck(
    payload: Any,
    *,
    expected_lesson: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """校验并净化课次 deck。返回 (clean_deck, warnings);致命错抛异常."""
    _check_spec_header(payload, what="课次文档")
    warnings: list[str] = []
    deck = copy.deepcopy(payload)
    deck["spec"] = spec.SPEC_VERSION
    deck["kind"] = spec.DOC_KIND_LESSON

    lesson_no = _as_int(deck.get("lesson"), 0)
    if expected_lesson is not None:
        if lesson_no != int(expected_lesson):
            if lesson_no <= 0:
                _warn(warnings, f"deck 缺少 lesson 编号,已按目标课次 {expected_lesson} 补齐")
                deck["lesson"] = int(expected_lesson)
            else:
                raise LessonDocValidationError(
                    f"deck 的课次编号 {lesson_no} 与目标课次 {expected_lesson} 不符"
                )
    elif lesson_no <= 0:
        raise LessonDocValidationError("deck 缺少有效的 lesson 编号")

    raw_slides = deck.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise LessonDocValidationError("deck 的 slides 缺失或为空")
    if len(raw_slides) > spec.MAX_SLIDES:
        _warn(warnings, f"页数 {len(raw_slides)} 超过 {spec.MAX_SLIDES},已截断")
        raw_slides = raw_slides[: spec.MAX_SLIDES]
    slides = []
    for i, slide in enumerate(raw_slides):
        cleaned = _validate_slide(slide, warnings, index=i)
        if cleaned is not None:
            slides.append(cleaned)
    if not slides:
        raise LessonDocValidationError("所有页均无法通过校验")
    _backfill_quiz_titles(slides, warnings)
    deck["slides"] = slides

    if "bg" in deck:
        deck_bg = clean_bg(deck.get("bg"), warnings, where="deck.bg")
        if deck_bg is None:
            deck.pop("bg", None)
        else:
            deck["bg"] = deck_bg
    if "globals" in deck:
        globals_clean = _validate_positioned(deck.get("globals"), warnings, where="globals")
        if len(globals_clean) > spec.MAX_GLOBALS:
            globals_clean = globals_clean[: spec.MAX_GLOBALS]
            _warn(warnings, f"全局元素超过 {spec.MAX_GLOBALS} 个已截断")
        for g in globals_clean:
            if "skipCovers" in g:
                g["skipCovers"] = bool(g["skipCovers"])
            raw_excludes = g.get("excludeSlides")
            excludes = [clean_id(x) for x in raw_excludes if clean_id(x)] if isinstance(raw_excludes, list) else []
            if excludes:
                g["excludeSlides"] = excludes
            else:
                g.pop("excludeSlides", None)
        if globals_clean:
            deck["globals"] = globals_clean
        else:
            deck.pop("globals", None)
    _dedupe_ids_and_prune_actions(deck, warnings)

    theme = _as_str(deck.get("theme")).strip().lower()
    if theme:
        parts = [p for p in re.split(r"[\s+]+", theme) if p]
        names = [p for p in parts if p in spec.THEMES]
        dark = "dark" in parts
        if not names and not dark:
            _warn(warnings, f"未知主题 '{theme}',已忽略")
            deck.pop("theme", None)
        else:
            deck["theme"] = (names[0] if names else spec.DEFAULT_THEME) + (" dark" if dark else "")
    return deck, warnings


def validate_manifest(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """校验并净化 course.json(首页清单)。"""
    _check_spec_header(payload, what="课程清单")
    warnings: list[str] = []
    manifest = copy.deepcopy(payload)
    manifest["spec"] = spec.SPEC_VERSION
    manifest["kind"] = spec.DOC_KIND_HOME

    course = manifest.get("course")
    if not isinstance(course, dict) or not _as_str(course.get("name")).strip():
        raise LessonDocValidationError("课程清单缺少 course.name")

    lessons_raw = manifest.get("lessons")
    if not isinstance(lessons_raw, list) or not lessons_raw:
        raise LessonDocValidationError("课程清单缺少 lessons")
    lessons: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, lesson in enumerate(lessons_raw):
        if not isinstance(lesson, dict):
            _warn(warnings, f"lessons[{i}] 不是对象,已丢弃")
            continue
        n = _as_int(lesson.get("n"), 0)
        if n <= 0 or n in seen:
            _warn(warnings, f"lessons[{i}] 编号无效或重复(n={lesson.get('n')}),已丢弃")
            continue
        seen.add(n)
        lesson = dict(lesson)
        lesson["n"] = n
        if _as_str(lesson.get("status")) not in {"ready", "pending"}:
            lesson["status"] = "pending"
        if not isinstance(lesson.get("topics"), list):
            lesson["topics"] = []
        lessons.append(lesson)
    if not lessons:
        raise LessonDocValidationError("课程清单没有任何有效课次")
    lessons.sort(key=lambda item: item["n"])
    manifest["lessons"] = lessons

    stages_raw = manifest.get("stages")
    stages: list[dict[str, Any]] = []
    if isinstance(stages_raw, list):
        covered: set[int] = set()
        for i, stage in enumerate(stages_raw):
            if not isinstance(stage, dict):
                continue
            stage = dict(stage)
            ns = [n for n in stage.get("lessons") or [] if _as_int(n, 0) in seen]
            ns = [_as_int(n, 0) for n in ns]
            # 同一课次只能属于一个阶段(先到先得),否则首页导图/卡片墙会重复渲染
            overlap = sorted({n for n in ns if n in covered})
            ns = [n for n in dict.fromkeys(ns) if n not in covered]
            if overlap:
                _warn(warnings, f"stages[{i}] 的课次 {overlap} 已属于前面的阶段,已去重")
            if not ns:
                _warn(warnings, f"stages[{i}] 未覆盖任何有效课次,已丢弃")
                continue
            stage["lessons"] = ns
            covered.update(ns)
            stages.append(stage)
        missing = sorted(seen - covered)
        if missing:
            stages.append({"label": "其他课次", "lessons": missing})
            _warn(warnings, f"课次 {missing} 未被任何阶段覆盖,已归入「其他课次」")
    if not stages:
        stages = [{"label": "全部课次", "lessons": sorted(seen)}]
        _warn(warnings, "课程清单缺少 stages,已按单一阶段生成")
    manifest["stages"] = stages

    tabs = manifest.get("tabs")
    if isinstance(tabs, list):
        cleaned_tabs = []
        for i, tab in enumerate(tabs):
            if not isinstance(tab, dict):
                continue
            tab = dict(tab)
            tab["blocks"] = _validate_blocks(tab.get("blocks"), warnings, where=f"tabs[{i}]")
            cleaned_tabs.append(tab)
        manifest["tabs"] = cleaned_tabs

    home = manifest.get("home")
    if home is not None:
        cleaned_home = _validate_home(home, warnings)
        if cleaned_home:
            manifest["home"] = cleaned_home
        else:
            manifest.pop("home", None)
    _dedupe_ids_and_prune_actions(manifest, warnings)
    return manifest, warnings


def _validate_home(home: Any, warnings: list[str]) -> dict[str, Any]:
    """manifest.home(首页编辑器数据,设计 §4.9):bg / style / sections。"""
    if not isinstance(home, dict):
        _warn(warnings, "home 不是对象,已忽略")
        return {}
    out: dict[str, Any] = {}
    bg = clean_bg(home.get("bg"), warnings, where="home.bg")
    if bg:
        out["bg"] = bg
    style = clean_home_style(home.get("style"), warnings)
    if style:
        out["style"] = style
    sections_raw = home.get("sections")
    if isinstance(sections_raw, list):
        sections: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for i, sec in enumerate(sections_raw):
            if not isinstance(sec, dict):
                continue
            key = _as_str(sec.get("key"))
            if key not in spec.HOME_SECTION_KEYS or key in seen_keys:
                _warn(warnings, f"home.sections[{i}]: 区块 '{key}' 未知或重复,已丢弃")
                continue
            seen_keys.add(key)
            clean: dict[str, Any] = {"key": key}
            if sec.get("hidden"):
                clean["hidden"] = True
            title = _as_str(sec.get("title")).strip()
            if title:
                clean["title"] = title[:60]
            if key == "hero" and isinstance(sec.get("stats"), list):
                clean["stats"] = [s for s in sec["stats"] if s in spec.HOME_STAT_KEYS]
            if key == "mindmap" and sec.get("collapsedDepth") is not None:
                clean["collapsedDepth"] = max(0, min(3, _as_int(sec.get("collapsedDepth"), 1)))
            if key == "blocks":
                clean["blocks"] = _validate_blocks(sec.get("blocks"), warnings, where=f"home.sections[{i}].blocks")
            sections.append(clean)
        for key in spec.HOME_SECTION_KEYS:      # 漏配的区块按默认顺序补在末尾,保证首页不丢内容
            if key not in seen_keys:
                sections.append({"key": key})
        out["sections"] = sections
    return out
