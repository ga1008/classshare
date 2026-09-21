"""Opt-in insight summaries; never load application, statistics or storage."""
from decimal import Decimal
from math import floor, isfinite
from .lq_components import _attrs, lq_props

INSIGHT_KINDS = ("avatar_stack", "insight_ring", "insight_bars", "insight_meter")
TONES = ("primary", "success", "warning", "danger", "info", "neutral", "indigo", "teal", "sky", "amber", "rose", "violet", "emerald")


def _mapping(value, keys):
    if not isinstance(value, dict) or value.keys() - set(keys):
        raise ValueError("Invalid LQ insight props")


def _text(value, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("LQ insights require plain text")
    return str(value)


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 9007199254740991 or not isfinite(value):
        raise ValueError("LQ insight values require nonnegative finite numbers or null")
    return value


def _numeric_text(value):
    if int(value) == value:
        return str(int(value))
    raw = repr(value)
    if "e" in raw and abs(value) >= 0.000001:
        return format(Decimal(raw), "f")
    return raw.replace("e-0", "e-").replace("e+0", "e+")


def _choice(value, choices):
    if value not in choices:
        raise ValueError("Invalid LQ insight choice")
    return value


def _array(value):
    if not isinstance(value, list):
        raise ValueError("LQ insights require arrays")
    return value


def _tone(value):
    return _choice(_text(value), TONES)


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _block(name, children=None, attrs=None, tag="div"):
    return _node(tag, {"class": name, **(attrs or {})}, children)


def _root(props, owned):
    attrs = {key: value for key, value in _attrs(props.get("attrs")).items() if not key.startswith("data-lq-") and key not in ("data-tone", "aria-label", "aria-labelledby")}
    if "id" in props:
        attrs["id"] = _text(props["id"], True)
    return {**attrs, **owned}


def _percent(value, total):
    return min(100, floor(value / total * 10000 + 0.5) / 100)


def _graphic(classes, value):
    return _block(classes, [_node("i", {"style": f"--lq-insight-percent: {_numeric_text(value)};"})], {"aria-hidden": "true"})


def _empty(state):
    return _block("lq-insight__empty", ["尚未提供统计数据。" if state == "missing" else "暂无可统计的数据。"], tag="p")


def lq_insight_props(kind, **props):
    if kind not in INSIGHT_KINDS:
        raise ValueError("Unknown LQ insight")
    common = ["id", "attrs"]
    if kind == "avatar_stack":
        _mapping(props, common + ["items", "size", "label"])
        size, label = props.get("size", 32), _text(props.get("label", "成员"), True)
        if type(size) is not int:
            raise ValueError("Invalid LQ avatar size")
        members = []
        for item in _array(props.get("items")):
            _mapping(item, ("name", "src", "detail"))
            avatar = {"name": _text(item.get("name"), True), "src": item.get("src"), "size": size}
            lq_props("avatar", **avatar)
            members.append({"avatar": avatar, "full": avatar["name"] + (f"（{_text(item['detail'])}）" if "detail" in item else "")})
        _choice(size, (24, 32, 40, 56))
        summary = label + "：" + ("；".join(member["full"] for member in members) if members else "暂无成员")
        visual = ([{"avatar": member["avatar"]} for member in members[:4]] + ([_block("lq-avatar-stack__more", [f"+{len(members)-4}"], tag="span")] if len(members) > 4 else [])) if members else ["暂无成员"]
        return _node("span", _root(props, {"class": f"lq-avatar-stack lq-avatar-stack--{size}" + ("" if members else " is-empty"),
                     "data-lq-insight": kind, "role": "img", "aria-label": summary}), [_block("lq-avatar-stack__visual", visual, {"aria-hidden": "true"}, "span")])
    shared = common + ["title", "caption", "tone", "zero"]
    _mapping(props, shared + (["value", "total", "value_label", "data_key"] if kind == "insight_ring" else ["value", "percent", "unit", "delta", "data_key"] if kind == "insight_meter" else ["items", "limit"]))
    title, description, tone = _text(props.get("title"), True), _text(props.get("caption", "")), _tone(props.get("tone", "indigo"))
    zero = _choice(props.get("zero", "empty"), ("empty", "value"))
    root = _root(props, {"class": "lq-insight", "data-lq-insight": kind, "data-tone": tone})
    key = _text(props.get("data_key", ""))
    if key:
        root["data-insight-key"] = key
    children = [_block("lq-insight__title", [title], tag="p")]
    if kind == "insight_ring":
        # Presence is explicit. Omitted required fields are not implicit nulls.
        if "value" not in props or "total" not in props:
            raise ValueError("Ring requires value and total")
        value, total, label = _number(props["value"]), _number(props["total"]), _text(props.get("value_label", ""))
        if value is not None and total is not None and value > total:
            raise ValueError("LQ ring value exceeds total")
        state = "missing" if value is None or total is None or total == 0 else "empty" if value == 0 and zero == "empty" else "value"
        if state == "value":
            visual = [_block("lq-ring__graphic", [_node("svg", {"viewBox": "0 0 36 36", "focusable": "false", "aria-hidden": "true"}, [
                _node("circle", {"class": "lq-ring__track", "cx": "18", "cy": "18", "r": "15.9155"}),
                _node("circle", {"class": "lq-ring__fill", "cx": "18", "cy": "18", "r": "15.9155", "pathLength": "100", "style": f"--lq-insight-percent: {_numeric_text(_percent(value,total))};"})]),
                _block("lq-ring__percent", [f"{floor(value / total * 100 + 0.5)}%"] )], {"aria-hidden": "true"})] if value > 0 else []
            children.append(_block("lq-ring", visual + [_block("lq-ring__value", [label or f"{_numeric_text(value)} / {_numeric_text(total)}"])]))
        else:
            children.append(_empty(state))
    elif kind == "insight_meter":
        if "value" not in props or "percent" not in props:
            raise ValueError("Meter requires value and percent")
        value, ratio = _number(props["value"]), _number(props["percent"])
        unit, delta = _text(props.get("unit", "")), _text(props.get("delta", ""))
        if ratio is not None and ratio > 100:
            raise ValueError("LQ meter percent exceeds 100")
        state = "missing" if value is None else "empty" if value == 0 and zero == "empty" else "value"
        if state == "value":
            children.append(_block("lq-meter", [_block("lq-meter__row", [_block("lq-meter__value", [_numeric_text(value) + unit], tag="span")] + ([_block("lq-meter__delta", [delta], tag="span")] if delta else []))]
                            + ([_graphic("lq-meter__track", min(100, ratio))] if value > 0 and ratio is not None and ratio > 0 else [])))
        else:
            children.append(_empty(state))
    else:
        limit = props.get("limit", 6)
        if isinstance(limit, bool) or not isinstance(limit, (float, int)) or not 0 <= limit <= 9007199254740991 or int(limit) != limit:
            raise ValueError("LQ bar limit must be a nonnegative integer")
        rows = []
        for item in _array(props.get("items")):
            _mapping(item, ("label", "value", "tone", "zero"))
            if "value" not in item:
                raise ValueError("Bar item requires a value")
            rows.append({"label": _text(item.get("label"), True), "value": _number(item["value"]), "tone": _tone(item.get("tone", tone)), "zero": _choice(item.get("zero", zero), ("empty", "value"))})
        rows = rows[:int(limit)]
        shown = [item for item in rows if item["value"] != 0 or item["zero"] == "value"]
        maximum = max([0] + [item["value"] or 0 for item in rows])
        state = "empty" if not rows or not shown else "missing" if all(item["value"] is None for item in shown) else "value"
        if state == "value":
            children.append(_node("ul", {"class": "lq-bars", "role": "list"}, [_block("lq-bars__row", [
                _node("span", {"class": "lq-bars__label"}, [item["label"]]), _node("span", {"class": "lq-bars__value"}, ["未提供" if item["value"] is None else _numeric_text(item["value"])])]
                + ([_graphic("lq-bars__track", _percent(item["value"], maximum))] if item["value"] is not None and item["value"] > 0 else []), {"data-tone": item["tone"]}, "li") for item in shown]))
        else:
            children.append(_empty(state))
    root["data-state"] = state
    if state != "value":
        root["class"] += " is-empty"
    children.append(_block("lq-insight__caption", [description, {"slot": "caption"}]))
    return _node("article", root, children)
