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
import re
from typing import Any

from . import spec

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SCRIPT_RE = re.compile(r"<\s*script[\s\S]*?<\s*/\s*script\s*>", re.IGNORECASE)
_FOREIGN_RE = re.compile(r"<\s*foreignObject[\s\S]*?<\s*/\s*foreignObject\s*>", re.IGNORECASE)
_EVENT_ATTR_RE = re.compile(r"\son[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*')")
_JS_HREF_RE = re.compile(r"(href|xlink:href)\s*=\s*([\"'])\s*javascript:[^\"']*\2", re.IGNORECASE)

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
    for pattern, label in ((_SCRIPT_RE, "script"), (_FOREIGN_RE, "foreignObject")):
        if pattern.search(text):
            text = pattern.sub("", text)
            _warn(warnings, f"{where}: svg 含 {label},已剥除")
    if _EVENT_ATTR_RE.search(text):
        text = _EVENT_ATTR_RE.sub("", text)
        _warn(warnings, f"{where}: svg 含事件属性,已剥除")
    if _JS_HREF_RE.search(text):
        text = _JS_HREF_RE.sub("", text)
        _warn(warnings, f"{where}: svg 含 javascript: 链接,已剥除")
    if _HEX_COLOR_RE.search(text):
        for hint, var in _HEX_HINTS:
            text = hint.sub(var, text)
        remaining = len(_HEX_COLOR_RE.findall(text))
        text = _HEX_COLOR_RE.sub(_hex_to_semantic_var, text)
        _warn(warnings, f"{where}: svg 含硬编码颜色 {remaining} 处,已替换为语义色")
    return text


def _media_src_ok(src: str) -> bool:
    if not src:
        return False
    lowered = src.lower()
    if lowered.startswith(("http://", "https://", "//", "/", "data:", "javascript:")):
        return False
    if src.startswith("..") and not src.startswith("../assets/"):
        return False
    if "\\" in src or "\x00" in src:
        return False
    return True


def _clean_items(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in value if item is not None]


def _validate_block(block: Any, warnings: list[str], *, where: str, depth: int = 0) -> dict[str, Any] | None:
    """返回净化后的块;无法修复返回 None(调用方丢弃并已记告警)."""
    if not isinstance(block, dict):
        _warn(warnings, f"{where}: 内容块不是对象,已丢弃")
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
    elif btype == "svg":
        out["body"] = sanitize_svg_body(out.get("body"), warnings, where=where)
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
        out["steps"] = steps
    return out


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
    layout = _as_str(out.get("layout"), spec.DEFAULT_LAYOUT)
    if layout not in spec.SLIDE_LAYOUTS:
        _warn(warnings, f"{where}: 未知版式 '{layout}',按 content 处理")
        layout = spec.DEFAULT_LAYOUT
    out["layout"] = layout
    if layout == "two-col":
        out["left"] = _validate_blocks(out.get("left"), warnings, where=f"{where}.left")
        out["right"] = _validate_blocks(out.get("right"), warnings, where=f"{where}.right")
        if not out["left"] and not out["right"]:
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
        if not areas:
            _warn(warnings, f"{where}: grid 版式无有效区域,已丢弃该页")
            return None
        out["areas"] = areas
    elif layout in {"title", "section"}:
        pass  # 纯字段版式,字段缺失由引擎容错
    else:
        out["blocks"] = _validate_blocks(out.get("blocks"), warnings, where=f"{where}.blocks")
        if layout == "content" and not out["blocks"]:
            _warn(warnings, f"{where}: 内容页无有效内容块,已丢弃该页")
            return None
    return out


def _check_spec_header(payload: Any, *, what: str) -> None:
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
    deck["slides"] = slides

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
    return manifest, warnings
