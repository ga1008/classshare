"""Pure typed view navigation. No application, database or global request state."""
import re

NAVIGATION_KINDS = ("tabs", "segment")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or "--lq-" in value:
        raise ValueError("Invalid LQ navigation id/key")
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LQ navigation requires text names")
    return str(value)


def _choice(value, choices):
    if value not in choices:
        raise ValueError("Invalid LQ navigation option")
    return value


def _node(tag, attrs, children=None):
    return {"tag": tag, "attrs": attrs, "children": children or []}


def lq_navigation_props(kind, **props):
    _choice(kind, NAVIGATION_KINDS)
    identity, label = _id(props.get("id")), _name(props.get("label"))
    orientation = _choice(props.get("orientation", "horizontal"), ("horizontal", "vertical"))
    activation = _choice(props.get("activation", "auto"), ("auto", "manual"))
    size = _choice(props.get("size", "md"), ("sm", "md"))
    variant = _choice(props.get("variant", "line"), ("line", "pill"))
    if kind == "segment" and (orientation != "horizontal" or variant != "line"):
        raise ValueError("Segment supports horizontal views only")
    source = props.get("items")
    if not isinstance(source, list) or len(source) < 2 or (kind == "segment" and len(source) > 5):
        raise ValueError("Invalid navigation item count")
    items, keys = [], set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("Invalid navigation item")
        key, name = _id(item.get("key")), _name(item.get("label"))
        if key in keys:
            raise ValueError("Duplicate navigation key")
        keys.add(key)
        disabled, panel, count = item.get("disabled", False), item.get("panel", ""), item.get("badge")
        if not isinstance(disabled, bool) or not isinstance(panel, str):
            raise ValueError("Invalid navigation item state/content")
        if count is not None and (isinstance(count, bool) or not isinstance(count, (int, float)) or not 0 <= count <= 9007199254740991 or int(count) != count):
            raise ValueError("Badge must be a nonnegative safe integer")
        items.append({"key": key, "label": name, "disabled": disabled, "panel": str(panel), "badge": int(count) if count is not None else None})
    selected = props.get("selected", next((item["key"] for item in items if not item["disabled"]), None))
    if not any(item["key"] == selected and not item["disabled"] for item in items):
        raise ValueError("Selected view must be an enabled item")
    tabs, panels = [], []
    for item in items:
        tab_id, panel_id = f'{identity}--lq-tab-{item["key"]}', f'{identity}--lq-panel-{item["key"]}'
        active = item["key"] == selected
        attrs = {"type": "button", "role": "tab", "class": "lq-tabs__tab", "id": tab_id,
                 "data-lq-tab": item["key"], "aria-controls": panel_id,
                 "aria-selected": str(active).lower(), "tabindex": "0" if active else "-1"}
        if item["disabled"]:
            attrs.update({"disabled": "", "aria-disabled": "true"})
        children = [_node("span", {"class": "lq-tabs__label"}, [item["label"]])]
        if item["badge"]:
            children.append({"component": "badge", "props": {"value": item["badge"]}})
        tabs.append(_node("button", attrs, children))
        panel_attrs = {"class": "lq-tabs__panel", "role": "tabpanel", "id": panel_id,
                       "aria-labelledby": tab_id, "tabindex": "0"}
        if not active:
            panel_attrs["hidden"] = ""
        panels.append(_node("section", panel_attrs, [{"slot": item["key"], "text": item["panel"]}]))
    classes = f'lq-tabs{" lq-segment" if kind == "segment" else ""} lq-tabs--{orientation} lq-tabs--{variant} lq-tabs--{size}'
    return _node("div", {"id": identity, "class": classes, "data-lq-tabs": "", "data-lq-activation": activation}, [
        _node("div", {"class": "lq-tabs__list", "role": "tablist", "aria-label": label, "aria-orientation": orientation}, tabs),
        _node("div", {"class": "lq-tabs__panels"}, panels),
    ])
