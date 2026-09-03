"""LessonDoc 2.1 编辑器模型的净化器:frame / style / bg / actions / id.

口径与 validate.py 一致:能修则修 + 记告警,修不了返回 None 由调用方丢弃。
所有取值都走白名单或数值裁剪,**绝不接受任意 CSS 字符串**——style 最终会
被引擎翻译成内联样式,这里就是注入防线。纯内存,不触库。
"""

from __future__ import annotations

import re
from typing import Any

from . import spec
from .paths import local_src_ok

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_ID_LEN = 40


def _warn(warnings: list[str], message: str) -> None:
    if len(warnings) < 200:
        warnings.append(message)


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n or n in (float("inf"), float("-inf")):
        return default
    return n


def _clamp(value: Any, rng: tuple[float, float], default: float | None = None) -> float | None:
    n = _num(value, None)
    if n is None:
        return default
    return max(rng[0], min(rng[1], n))


def _int_or_float(n: float) -> int | float:
    return int(n) if float(n).is_integer() else round(n, 2)


# ---------------------------------------------------------------- id

def clean_id(value: Any) -> str:
    """规范化 id:只留 \\w 与 -,截断到 40;非法/空返回 ''。"""
    if value is None:
        return ""
    text = _ID_RE.sub("", str(value).strip())
    return text[:_MAX_ID_LEN]


# ---------------------------------------------------------------- 颜色

def clean_color(value: Any) -> str | None:
    """#hex 或语义色名;其他一律拒绝(返回 None)。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _HEX_RE.match(text):
        return text.lower()
    lowered = text.lower()
    if lowered in spec.STYLE_SEMANTIC_COLORS:
        return lowered
    return None


def clean_gradient(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    start = clean_color(value.get("from"))
    end = clean_color(value.get("to"))
    if not start or not end:
        return None
    angle = _clamp(value.get("angle", 135), (0, 360), 135)
    return {"from": start, "to": end, "angle": _int_or_float(angle)}


# ---------------------------------------------------------------- frame

def clean_frame(value: Any, warnings: list[str], *, where: str) -> dict[str, Any] | None:
    """定位框:x/y 必填(缺 w/h 给默认),越界裁剪,r/z 可选。"""
    if not isinstance(value, dict):
        return None
    x = _num(value.get("x"))
    y = _num(value.get("y"))
    if x is None or y is None:
        _warn(warnings, f"{where}: frame 缺少 x/y,已丢弃定位")
        return None
    w = _num(value.get("w"), 320.0)
    h = _num(value.get("h"), 120.0)
    cx = max(spec.FRAME_X_RANGE[0], min(spec.FRAME_X_RANGE[1], x))
    cy = max(spec.FRAME_Y_RANGE[0], min(spec.FRAME_Y_RANGE[1], y))
    cw = max(spec.FRAME_SIZE_RANGE[0], min(spec.FRAME_SIZE_RANGE[1], w))
    ch = max(spec.FRAME_SIZE_RANGE[0], min(spec.FRAME_SIZE_RANGE[1], h))
    if (cx, cy, cw, ch) != (x, y, w, h):
        _warn(warnings, f"{where}: frame 超出范围,已裁剪")
    out: dict[str, Any] = {
        "x": _int_or_float(cx), "y": _int_or_float(cy),
        "w": _int_or_float(cw), "h": _int_or_float(ch),
    }
    r = _num(value.get("r"))
    if r is not None and r != 0:
        out["r"] = _int_or_float(((r + 180) % 360) - 180)
    z = _num(value.get("z"))
    if z is not None and z != 0:
        out["z"] = int(max(-100, min(1000, z)))
    return out


# ---------------------------------------------------------------- style

_STYLE_KEYS = frozenset(
    {
        "font", "size", "weight", "italic", "color", "gradient", "stroke", "shadow",
        "align", "lineHeight", "letterSpacing", "opacity", "bg", "bgGradient",
        "border", "padding",
    }
)


def clean_style(value: Any, warnings: list[str], *, where: str) -> dict[str, Any] | None:
    """样式白名单净化;空结果返回 None(调用方 pop 掉 style)。"""
    if not isinstance(value, dict):
        if value is not None:
            _warn(warnings, f"{where}: style 不是对象,已忽略")
        return None
    out: dict[str, Any] = {}
    dropped: list[str] = []

    font = value.get("font")
    if font is not None:
        if str(font) in spec.STYLE_FONTS:
            out["font"] = str(font)
        else:
            dropped.append("font")

    if value.get("size") is not None:
        size = _clamp(value.get("size"), spec.STYLE_SIZE_RANGE)
        if size is None:
            dropped.append("size")
        else:
            out["size"] = _int_or_float(size)

    weight = value.get("weight")
    if weight is not None:
        try:
            w_int = int(weight)
        except (TypeError, ValueError):
            w_int = None
        if w_int in spec.STYLE_WEIGHTS:
            out["weight"] = w_int
        else:
            dropped.append("weight")

    if value.get("italic") is not None:
        out["italic"] = bool(value.get("italic"))

    for key in ("color", "bg"):
        if value.get(key) is not None:
            color = clean_color(value.get(key))
            if color:
                out[key] = color
            else:
                dropped.append(key)

    for key in ("gradient", "bgGradient"):
        if value.get(key) is not None:
            grad = clean_gradient(value.get(key))
            if grad:
                out[key] = grad
            else:
                dropped.append(key)

    stroke = value.get("stroke")
    if stroke is not None:
        if isinstance(stroke, dict):
            sw = _clamp(stroke.get("width", 1), spec.STYLE_STROKE_WIDTH_RANGE, 1)
            sc = clean_color(stroke.get("color")) or "text"
            out["stroke"] = {"width": _int_or_float(sw), "color": sc}
        else:
            dropped.append("stroke")

    shadow = value.get("shadow")
    if shadow is not None:
        if str(shadow) in spec.STYLE_SHADOWS:
            out["shadow"] = str(shadow)
        else:
            dropped.append("shadow")

    align = value.get("align")
    if align is not None:
        if str(align) in spec.STYLE_ALIGNS:
            out["align"] = str(align)
        else:
            dropped.append("align")

    for key, rng in (
        ("lineHeight", spec.STYLE_LINE_HEIGHT_RANGE),
        ("letterSpacing", spec.STYLE_LETTER_SPACING_RANGE),
        ("opacity", (0.0, 1.0)),
        ("padding", spec.STYLE_PADDING_RANGE),
    ):
        if value.get(key) is not None:
            n = _clamp(value.get(key), rng)
            if n is None:
                dropped.append(key)
            else:
                out[key] = _int_or_float(n)

    border = value.get("border")
    if border is not None:
        if isinstance(border, dict):
            bw = _clamp(border.get("width", 1), spec.STYLE_BORDER_WIDTH_RANGE, 1)
            bc = clean_color(border.get("color")) or "muted"
            br = _clamp(border.get("radius", 0), spec.STYLE_RADIUS_RANGE, 0)
            bs = str(border.get("style") or "solid")
            if bs not in spec.STYLE_BORDER_STYLES:
                bs = "solid"
            out["border"] = {
                "width": _int_or_float(bw), "color": bc,
                "radius": _int_or_float(br), "style": bs,
            }
        else:
            dropped.append("border")

    unknown = sorted(str(k) for k in value.keys() if k not in _STYLE_KEYS)
    if unknown:
        dropped.extend(unknown)
    if dropped:
        _warn(warnings, f"{where}: style 中不受支持或取值非法的键已忽略: {', '.join(dropped)}")
    return out or None


# ---------------------------------------------------------------- 背景

def _bg_image_src_ok(src: str) -> bool:
    return local_src_ok(src)


def clean_home_style(value: Any, warnings: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    base = {k: v for k, v in value.items() if k not in {"heroGradient", "bgGradient", "cardRadius"}}
    out = clean_style(base, warnings, where="home.style") or {}
    gradient = clean_gradient(value.get("heroGradient")) or clean_gradient(value.get("bgGradient"))
    if gradient:
        out["heroGradient"] = gradient
    radius = _clamp(value.get("cardRadius"), spec.STYLE_RADIUS_RANGE)
    if radius is not None:
        out["cardRadius"] = _int_or_float(radius)
    return out or None


def clean_bg(value: Any, warnings: list[str], *, where: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        if value is not None:
            _warn(warnings, f"{where}: bg 不是对象,已忽略")
        return None
    out: dict[str, Any] = {}
    color = clean_color(value.get("color"))
    if color:
        out["color"] = color
    elif value.get("color") is not None:
        _warn(warnings, f"{where}: bg.color 非法,已忽略")
    grad = clean_gradient(value.get("gradient"))
    if grad:
        out["gradient"] = grad
    image = value.get("image")
    if isinstance(image, dict):
        src = str(image.get("src") or "").strip()
        if not _bg_image_src_ok(src):
            _warn(warnings, f"{where}: bg.image 路径不合规('{src[:80]}'),已忽略(只允许包内相对路径)")
        else:
            fit = str(image.get("fit") or "cover")
            if fit not in spec.BG_FITS:
                fit = "cover"
            img: dict[str, Any] = {
                "src": src,
                "fit": fit,
                "scale": _int_or_float(_clamp(image.get("scale", 100), spec.BG_SCALE_RANGE, 100)),
                "x": _int_or_float(_clamp(image.get("x", 50), (0, 100), 50)),
                "y": _int_or_float(_clamp(image.get("y", 50), (0, 100), 50)),
                "rotate": _int_or_float(_clamp(image.get("rotate", 0), (-180, 180), 0)),
                "opacity": _int_or_float(_clamp(image.get("opacity", 1), spec.BG_OPACITY_RANGE, 1)),
            }
            blur = _clamp(image.get("blur", 0), spec.BG_BLUR_RANGE, 0)
            if blur:
                img["blur"] = _int_or_float(blur)
            out["image"] = img
    tint = value.get("tint")
    if isinstance(tint, dict):
        tc = clean_color(tint.get("color"))
        if tc:
            out["tint"] = {
                "color": tc,
                "opacity": _int_or_float(_clamp(tint.get("opacity", 0.3), (0.0, 1.0), 0.3)),
            }
    return out or None


# ---------------------------------------------------------------- 动作

def clean_actions(value: Any, warnings: list[str], *, where: str) -> list[dict[str, Any]]:
    """动作列表净化(不检查目标是否存在——那需要全 deck 视角,见 prune_dangling_actions)。"""
    if value is None:
        return []
    if not isinstance(value, list):
        _warn(warnings, f"{where}: actions 不是数组,已忽略")
        return []
    out: list[dict[str, Any]] = []
    for i, step in enumerate(value):
        if len(out) >= spec.MAX_ACTIONS_PER_BLOCK:
            _warn(warnings, f"{where}: 动作超过 {spec.MAX_ACTIONS_PER_BLOCK} 步已截断")
            break
        if not isinstance(step, dict):
            continue
        kind = str(step.get("do") or "").strip()
        if kind not in spec.ACTION_KINDS:
            _warn(warnings, f"{where}.actions[{i}]: 未知动作 '{kind}',已丢弃")
            continue
        clean: dict[str, Any] = {"do": kind}
        if kind in spec.ACTION_TARGET_KINDS:
            target = clean_id(step.get("target"))
            if not target:
                _warn(warnings, f"{where}.actions[{i}]: {kind} 缺少 target,已丢弃")
                continue
            clean["target"] = target
        if kind == "move":
            clean["dx"] = _int_or_float(_clamp(step.get("dx", 0), (-2000, 2000), 0))
            clean["dy"] = _int_or_float(_clamp(step.get("dy", 0), (-2000, 2000), 0))
        elif kind == "moveTo":
            clean["x"] = _int_or_float(_clamp(step.get("x", 0), spec.FRAME_X_RANGE, 0))
            clean["y"] = _int_or_float(_clamp(step.get("y", 0), spec.FRAME_Y_RANGE, 0))
        elif kind == "goto":
            slide_id = clean_id(step.get("slideId"))
            slide_no = _num(step.get("slide"))
            if not slide_id and (slide_no is None or slide_no < 1):
                _warn(warnings, f"{where}.actions[{i}]: goto 缺少有效页码,已丢弃")
                continue
            if slide_id:
                clean["slideId"] = slide_id
            if slide_no is not None and slide_no >= 1:
                clean["slide"] = int(slide_no)
        if step.get("ms") is not None:
            clean["ms"] = int(_clamp(step.get("ms"), spec.ACTION_MS_RANGE, 400) or 0)
        ease = step.get("ease")
        if ease is not None and str(ease) in spec.ACTION_EASES:
            clean["ease"] = str(ease)
        out.append(clean)
    return out


def prune_dangling_actions(
    container: dict[str, Any], known_ids: set[str], warnings: list[str], *, where: str
) -> None:
    """删除指向不存在 id 的动作步(原地修改)。"""
    actions = container.get("actions")
    if not isinstance(actions, list) or not actions:
        return
    kept = []
    for step in actions:
        target = step.get("target")
        if target and target not in known_ids:
            _warn(warnings, f"{where}: 动作目标 '{target}' 不存在,已删除该步")
            continue
        kept.append(step)
    if kept:
        container["actions"] = kept
    else:
        container.pop("actions", None)
