"""Pure content trees. Slots belong to authors/controllers, never raw HTML props."""
from collections.abc import Mapping
import math
import re

from .lq_components import lq_props as presentation_props, _url
from .lq_forms import lq_form_props

CONTENT_KINDS = ("card", "list", "row", "empty", "page_head", "filter_bar", "prose", "bubble")
_ATTR = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.:-]*\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_REASONS = {
    "empty": "暂无内容", "no-results": "没有匹配的结果", "error": "内容加载失败",
    "forbidden": "没有访问权限", "offline": "当前处于离线状态",
}


def _text(value, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("Content requires plain text")
    return str(value)


def _id(value):
    value = _text(value, True)
    if not _ID.fullmatch(value) or "--lq-" in value:
        raise ValueError("Content requires a unique non-reserved id")
    return value


def _flag(p, key, default=False):
    value = p.get(key, default)
    if not isinstance(value, bool):
        raise ValueError("Content flags must be boolean")
    return value


def _choice(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("Invalid content variant")
    return str(value)


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Content attrs must be a mapping")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not (_ATTR.fullmatch(key) or key in ("id", "title")):
            raise ValueError("Unsupported content attribute")
        if item is None:
            continue
        if not isinstance(item, (str, bool)):
            raise ValueError("Content attribute values must be text or boolean")
        if key.startswith("data-lq-") or key in ("aria-hidden", "aria-live", "aria-label", "aria-labelledby"):
            continue
        result[key] = str(item).lower() if isinstance(item, bool) else str(item)
    if "id" in result:
        result["id"] = _id(result["id"])
    return result


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _block(classes, children=None, tag="div", attrs=None):
    return _node(tag, {"class": classes, **(attrs or {})}, children)


def _slot(name, classes):
    return _block(classes, [{"slot": name}], attrs={"data-lq-slot": name})


def _action(value):
    if not isinstance(value, Mapping):
        raise ValueError("Actions must be structured Button props")
    allowed = {"label", "href", "id", "attrs", "variant", "size", "icon", "disabled", "ariaDisabled", "loading", "type"}
    if set(value) - allowed:
        raise ValueError("Unknown action property")
    p = {"label": _text(value.get("label")), "variant": value.get("variant", "soft"),
         "size": value.get("size", "sm"), "href": value.get("href"), "id": value.get("id"),
         "icon": value.get("icon"), "disabled": value.get("disabled", False),
         "ariaDisabled": value.get("ariaDisabled", False), "loading": value.get("loading", False),
         "type": value.get("type", "button"), "attrs": value.get("attrs")}
    # Delegate all Button safety, naming, URL and state semantics to the P1 helper.
    normalized = presentation_props("button", **{("aria_disabled" if key == "ariaDisabled" else key): item for key, item in p.items()})
    p.update({"href": normalized["attrs"].get("href"), "label": normalized["label"], "icon": normalized["icon"]})
    if p["id"] is not None:
        p["id"] = _id(p["id"])
    p["attrs"] = {} if p["attrs"] is None else {str(key): (str(item).lower() if isinstance(item, bool) else str(item)) for key, item in p["attrs"].items() if item is not None}
    return {"button": p}


def _actions(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("Actions must be a list")
    return [_action(item) for item in value]


def lq_content_props(component, **p):
    if component not in CONTENT_KINDS:
        raise ValueError("Unknown content component")
    if any(key in p for key in ("html", "rawHTML", "raw_html")):
        raise ValueError("Content does not accept raw HTML")
    if ("swipe" in p and component != "row") or ("groups" in p and component != "list"):
        raise ValueError("Grouped lists and swipe Rows have distinct props")
    attrs = _attrs(p.get("attrs"))
    if p.get("id") is not None:
        attrs["id"] = _id(p["id"])
    attrs["class"] = "lq-" + component.replace("_", "-")
    if component in ("card", "row"):
        title = _text(p.get("title"), True)
        primary = p.get("primary")
        heading = [title]
        if primary is not None:
            if not isinstance(primary, Mapping) or set(primary) - {"href", "id", "attrs", "disabled"}:
                raise ValueError("Primary action accepts href/id/attrs/disabled only")
            heading = [_action({**primary, "label": title, "variant": "link", "size": "md"})]
            attrs["class"] += " lq-" + component + "--interactive"
        meta = _text(p.get("meta"))
        if component == "card":
            variant = _choice(p.get("variant", "default"), ("default", "flat", "stat", "hero"))
            attrs["class"] += " lq-surface lq-card--" + variant
            children = [_block("lq-card__head", [_block("lq-card__copy", [
                _block("lq-card__title", heading, "h3"), *([_block("lq-card__meta", [meta], "p")] if meta else [])]),
                _block("lq-card__actions", _actions(p.get("actions")) + [{"slot": "actions"}], attrs={"data-lq-slot": "actions"})])]
            if variant == "stat":
                value = p.get("value")
                if isinstance(value, bool) or not isinstance(value, (str, int, float)) or (isinstance(value, str) and not value.strip()):
                    raise ValueError("Stat cards require an explicit value")
                if not isinstance(value, str) and (not math.isfinite(value) or int(value) != value or abs(value) > 9007199254740991):
                    raise ValueError("Stat numbers must be safe integers; format other values as text")
                children.append(_block("lq-card__value", [str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)], "p"))
            children += [_slot("body", "lq-card__body"), _slot("foot", "lq-card__foot")]
            return _node("article", attrs, children)
        if _flag(p, "unread"):
            attrs["class"] += " is-unread"
        children = [_slot("lead", "lq-row__lead"), _block("lq-row__main", [_block("lq-row__title", heading, "p"), *([_block("lq-row__meta", [meta], "p")] if meta else [])]), _block("lq-row__trail", _actions(p.get("actions")) + [{"slot": "trail"}], attrs={"data-lq-slot": "trail"})]
        if "swipe" in p:
            swipe = p["swipe"]
            if not isinstance(swipe, Mapping) or set(swipe) - {"key", "label", "disabled", "busy"}:
                raise ValueError("Swipe requires structured intent props")
            row_id = _id(attrs.get("id"))
            key, label = _id(swipe.get("key")), _text(swipe.get("label"), True)
            disabled, busy = _flag(swipe, "disabled"), _flag(swipe, "busy")
            attrs.update({"class": attrs["class"] + " lq-row--swipe", "data-lq-row-key": key,
                          "data-lq-row-disabled": str(disabled).lower(), "data-lq-row-busy": str(busy).lower(), "aria-busy": str(busy).lower()})
            children[-1]["children"].append(_action({"label": "显示操作", "variant": "ghost", "disabled": disabled or busy,
                "attrs": {"data-lq-row-reveal": "", "aria-label": "显示" + label + "操作", "aria-expanded": "false", "aria-controls": row_id + "--lq-swipe"}}))
            children = [_block("lq-row__front", children), _block("lq-row__swipe-actions", [_action({
                "label": label, "variant": "destructive", "disabled": disabled or busy,
                "attrs": {"data-lq-row-action": key}})], attrs={"id": row_id + "--lq-swipe"})]
        return _node("li", attrs, children)
    if component == "list":
        label = _text(p.get("label"), True)
        items = p.get("items", [])
        if not isinstance(items, (list, tuple)) or any(not isinstance(item, Mapping) for item in items):
            raise ValueError("List items must be Row props")
        attrs["aria-label"] = label
        attrs["role"] = "list"  # Retain list semantics when the marker is visually removed.
        tag = "ol" if _flag(p, "ordered") else "ul"
        if "groups" not in p:
            return _node(tag, attrs, [lq_content_props("row", **item) for item in items] + [{"slot": "items"}])
        groups = p["groups"]
        if items or not isinstance(groups, (list, tuple)):
            raise ValueError("Grouped lists require groups and no flat items")
        list_id, seen, children = _id(attrs.get("id")), set(), []
        attrs["class"] += " lq-list--grouped"
        for group in groups:
            if not isinstance(group, Mapping) or set(group) - {"key", "title", "items"}:
                raise ValueError("Invalid list group")
            key, title = _id(group.get("key")), _text(group.get("title"), True)
            if key in seen:
                raise ValueError("List group keys must be unique")
            seen.add(key)
            group_items = group.get("items", [])
            if not isinstance(group_items, (list, tuple)) or any(not isinstance(item, Mapping) for item in group_items):
                raise ValueError("Group items must be Row props")
            heading_id = list_id + "--lq-group-" + key
            children.append(_block("lq-list__group", [
                _block("lq-list__heading", [title], "h3", {"id": heading_id}),
                _node(tag, {"class": "lq-list__items", "role": "list", "aria-labelledby": heading_id},
                      [lq_content_props("row", **item) for item in group_items] + [{"slot": "items:" + key}])], "li"))
        return _node(tag, attrs, children)
    if component == "empty":
        reason = _choice(p.get("reason", "empty"), _REASONS)
        variant = _choice(p.get("variant", "inline"), ("inline", "card", "page"))
        attrs.update({"class": attrs["class"] + " lq-empty--" + variant, "data-reason": reason, "data-page-empty": ""})
        title = _text(p.get("title", _REASONS[reason]), True)
        description = _text(p.get("description"))
        return _node("div", attrs, [_block("lq-empty__copy", [_block("lq-empty__title", [title], "strong"), *([_block("lq-empty__description", [description], "p")] if description else [])]), _block("lq-empty__actions", _actions(p.get("actions")) + [{"slot": "actions"}], attrs={"data-lq-slot": "actions"})])
    if component == "page_head":
        title = _text(p.get("title"), True)
        description, explain = _text(p.get("description")), _text(p.get("explain"))
        _text(p.get("eyebrow"))  # Kept for signature compatibility; intentionally unrendered.
        title_attrs = {"id": _id(p["title_id"])} if p.get("title_id") else {}
        heading = [_block("lq-page-head__title", [title], "h2", title_attrs)]
        if explain:
            heading += [_action({"label": "", "icon": "circle-help", "variant": "ghost", "attrs": {"aria-label": _text(p.get("explain_label")) or title + "说明", "aria-haspopup": "dialog", "data-explain": "", "data-explain-toggle": "", "data-explain-title": title, "data-explain-text": explain, "data-explain-placement": "bottom"}})]
        attrs["data-page-head"] = ""
        attrs["class"] += " lq-surface lq-scene-heading"
        page_actions = p.get("actions")
        if isinstance(page_actions, (list, tuple)):
            page_actions = [{**item, "variant": {"primary": "prominent", "outline": "soft"}.get(item.get("variant"), item.get("variant", "soft"))} if isinstance(item, Mapping) else item for item in page_actions]
        return _node("header", attrs, [_block("page-head__copy", [_block("lq-page-head__title-row", heading), *([_block("page-head__desc", [description], "p")] if description else [])]), _slot("aside", "page-head__aside"), _block("page-head__actions", _actions(page_actions) + [{"slot": "actions"}], attrs={"data-lq-slot": "actions"})])
    if component == "filter_bar":
        tag = _choice(p.get("tag", "form"), ("form", "div"))
        attrs.update({"data-filter-bar": "", "aria-label": _text(p.get("label", "筛选"), True)})
        if tag == "form":
            attrs["method"] = _choice(p.get("method", "get"), ("get", "post"))
            if p.get("action") is not None:
                attrs["action"] = _url(p["action"], image=True)
        else:
            if p.get("action") is not None or "method" in p:
                raise ValueError("A grouped FilterBar cannot submit")
            attrs["role"] = "group"
        children = []
        if p.get("search_id"):
            children.append(_block("lq-filter-bar__search", [lq_form_props("input", id=p["search_id"], label=_text(p.get("search_label", "搜索"), True), type="search", name=p.get("search_name", "q"), value=_text(p.get("search_value")), placeholder=_text(p.get("search_placeholder", "搜索…")), attrs=p.get("search_attrs"))]))
        elif p.get("search_attrs"):
            raise ValueError("Search attributes require a search id")
        return _node(tag, attrs, children + [_slot("filters", "lq-filter-bar__controls"), _slot("actions", "lq-filter-bar__actions")])
    if component == "prose":
        return _node("div", attrs, [_text(p.get("text")), {"slot": "content"}])
    side = _choice(p.get("side", "incoming"), ("incoming", "outgoing"))
    author, time = _text(p.get("author"), True), _text(p.get("time"), True)
    attrs["class"] += " lq-bubble--" + side + (" is-connected" if _flag(p, "connected") else "")
    time_attrs = {"datetime": _text(p["datetime"], True)} if p.get("datetime") is not None else {}
    return _node("article", attrs, [_block("lq-bubble__author", [author], "p"), _block("lq-bubble__content", [_text(p.get("text")), {"slot": "content"}], attrs={"data-lq-slot": "content"}), _block("lq-bubble__time", [time], "time", time_attrs)])
