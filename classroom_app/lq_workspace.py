"""Pure split/viewer presentation; existing content and controllers own data."""
from .lq_components import _attrs
from .lq_navigation import lq_navigation_props, _id, _name, _node

WORKSPACE_KINDS = ("split", "viewer")


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 160 <= value <= 3200 or int(value) != value:
        raise ValueError("Workspace size must be a bounded integer")
    return int(value)


def lq_workspace_props(component, **props):
    allowed = {"id", "label", "sideLabel", "mainLabel", "width", "min", "max", "mainMin", "selected", "attrs"} if component == "split" else {"id", "title", "kind", "attrs"}
    if component not in WORKSPACE_KINDS or props.keys() - allowed:
        raise ValueError("Invalid workspace props")
    identity = _id(props.get("id"))
    attrs = _attrs(props.get("attrs"))
    attrs = {key: value for key, value in attrs.items() if not key.startswith("data-lq-") and key not in {"id", "role", "aria-label", "aria-labelledby", "aria-hidden", "aria-live"}}
    if component == "viewer":
        kind = props.get("kind", "document")
        if kind not in ("document", "iframe"):
            raise ValueError("Unknown viewer kind")
        return _node("section", {**attrs, "id": identity, "class": f"lq-viewer lq-viewer--{kind}", "aria-labelledby": f"{identity}--lq-title"}, [
            _node("header", {"class": "lq-viewer__toolbar lq-glass"}, [_node("h2", {"id": f"{identity}--lq-title", "class": "lq-viewer__title"}, [_name(props.get("title"))]), _node("div", {"class": "lq-viewer__actions"}, [{"slot": "actions", "text": ""}])]),
            _node("div", {"class": "lq-viewer__content"}, [{"slot": "content", "text": ""}]),
        ])
    minimum, maximum = _integer(props.get("min", 160)), _integer(props.get("max", 640))
    width, main_min = _integer(props.get("width", 280)), _integer(props.get("mainMin", 320))
    if not minimum <= width <= maximum:
        raise ValueError("Invalid workspace width bounds")
    selected = props.get("selected", "main")
    tree = lq_navigation_props("segment", id=identity, label=_name(props.get("label")), selected=selected,
                               items=[{"key": "side", "label": _name(props.get("sideLabel"))}, {"key": "main", "label": _name(props.get("mainLabel"))}])
    tree["attrs"].update({**attrs, "class": "lq-split lq-tabs lq-segment", "data-lq-split": "", "data-lq-split-width": str(width), "data-lq-split-min": str(minimum), "data-lq-split-max": str(maximum), "data-lq-split-main-min": str(main_min), "data-lq-split-selected": selected})
    del tree["attrs"]["data-lq-tabs"]
    tree["children"][0]["attrs"]["hidden"] = ""
    panels = tree["children"][1]
    panels["attrs"]["class"] = "lq-split__panes"
    for index, panel in enumerate(panels["children"]):
        key, label = ("main", props["mainLabel"]) if index else ("side", props["sideLabel"])
        panel["attrs"].update({"class": f"lq-split__pane lq-split__pane--{key}", "role": "region", "aria-labelledby": f"{identity}--lq-heading-{key}", "data-lq-split-pane": key})
        panel["attrs"].pop("hidden", None)
        panel["children"].insert(0, _node("h2", {"id": f"{identity}--lq-heading-{key}", "class": "lq-split__heading"}, [label]))
    panels["children"].insert(1, _node("div", {"class": "lq-split__separator", "hidden": "", "role": "separator", "tabindex": "0", "aria-orientation": "vertical", "aria-label": f'调整{props["sideLabel"]}宽度', "aria-controls": f"{identity}--lq-panel-side", "aria-valuemin": str(minimum), "aria-valuemax": str(maximum), "aria-valuenow": str(width)}))
    return tree
