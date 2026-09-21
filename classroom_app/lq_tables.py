"""Pure table presentation; sorting, requests and selection data stay with callers."""
from collections.abc import Mapping
import math
import re
from .lq_components import lq_props as presentation_props, _url

TABLE_KINDS = ("table", "pager", "bulk_bar", "result_count")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
_ATTR = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.:-]*\Z")


def _text(value, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError("Table text must be plain text")
    return str(value)


def _number(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not minimum <= value <= 9007199254740991:
        raise ValueError("Table counts must be safe integers")
    return int(value)


def _key(value, identity=False):
    value = _text(value, True)
    if not (_ID if identity else _KEY).fullmatch(value) or "--lq-" in value:
        raise ValueError("A stable non-reserved key is required")
    return value


def _flag(p, key, default=False):
    value = p.get(key, default)
    if not isinstance(value, bool):
        raise ValueError("Table flags must be boolean")
    return value


def _choice(value, values):
    if not isinstance(value, str) or value not in values:
        raise ValueError("Invalid table variant")
    return value


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Table attrs must be a mapping")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not (_ATTR.fullmatch(key) or key in ("id", "title")):
            raise ValueError("Unsupported table attribute")
        if item is None:
            continue
        if not isinstance(item, (str, bool)):
            raise ValueError("Table attribute values must be text or boolean")
        if key.startswith("data-lq-") or key in ("id", "aria-label", "aria-labelledby", "aria-live", "aria-hidden", "aria-sort", "aria-checked", "aria-busy"):
            continue
        result[key] = str(item).lower() if isinstance(item, bool) else str(item)
    return result


def _node(tag, attrs=None, children=None):
    return {"tag": tag, "attrs": attrs or {}, "children": children or []}


def _button(value):
    if not isinstance(value, Mapping) or set(value) - {"label", "href", "id", "attrs", "variant", "disabled", "loading"}:
        raise ValueError("Bulk actions require structured Button props")
    props = {"label": _text(value.get("label"), True), "href": value.get("href"), "id": value.get("id"),
             "attrs": value.get("attrs"), "variant": value.get("variant", "soft"), "size": "sm", "icon": None,
             "disabled": value.get("disabled", False), "ariaDisabled": False, "loading": value.get("loading", False), "type": "button"}
    normalized = presentation_props("button", **{("aria_disabled" if key == "ariaDisabled" else key): item for key, item in props.items()})
    props["href"] = normalized["attrs"].get("href")
    if props["id"] is not None:
        props["id"] = _key(props["id"], True)
    props["attrs"] = {} if props["attrs"] is None else {str(key): (str(item).lower() if isinstance(item, bool) else str(item)) for key, item in props["attrs"].items() if item is not None}
    return {"button": props}


def _checkbox(label, attrs):
    return _node("label", {"class": "lq-table__check"}, [_node("input", {"type": "checkbox", "aria-label": label, **attrs})])


def _table(p, attrs):
    identity, caption = _key(p.get("id"), True), _text(p.get("caption"), True)
    mode = _choice(p.get("mode", "record"), ("record", "matrix"))
    density = _choice(p.get("density", "comfortable"), ("comfortable", "dense"))
    selectable = _flag(p, "selectable")
    columns, rows = p.get("columns"), p.get("rows", [])
    if not isinstance(columns, (list, tuple)) or not columns or not isinstance(rows, (list, tuple)):
        raise ValueError("Table columns and rows must be lists")
    cols, keys, active_sorts, row_headers = [], set(), 0, 0
    for item in columns:
        if not isinstance(item, Mapping) or set(item) - {"key", "label", "align", "sortable", "sort", "rowHeader", "slot"}:
            raise ValueError("Invalid table column")
        key = _key(item.get("key"))
        if key in keys:
            raise ValueError("Duplicate table column key")
        keys.add(key)
        col = {"key": key, "label": _text(item.get("label"), True), "align": _choice(item.get("align", "start"), ("start", "end")), "sortable": _flag(item, "sortable"), "sort": _choice(item.get("sort", "none"), ("none", "ascending", "descending")), "rowHeader": _flag(item, "rowHeader"), "slot": _flag(item, "slot")}
        if col["sort"] != "none":
            if not col["sortable"]:
                raise ValueError("Only sortable columns have sort state")
            active_sorts += 1
        row_headers += int(col["rowHeader"])
        cols.append(col)
    if active_sorts > 1 or row_headers > 1:
        raise ValueError("Table supports one active sort and one row header")
    normalized_rows, row_keys = [], set()
    for item in rows:
        if not isinstance(item, Mapping) or set(item) - {"key", "label", "cells", "selected", "disabled"}:
            raise ValueError("Invalid table row")
        key = _key(item.get("key"))
        if key in row_keys:
            raise ValueError("Duplicate table row key")
        row_keys.add(key)
        cells = item.get("cells")
        if not isinstance(cells, Mapping) or set(cells) != keys:
            raise ValueError("Every row must provide exactly the declared cells")
        values = {}
        for name, value in cells.items():
            values[name] = str(_number(value, -9007199254740991)) if isinstance(value, (int, float)) and not isinstance(value, bool) else _text(value)
        normalized_rows.append({"key": key, "label": _text(item.get("label"), selectable), "cells": values, "selected": _flag(item, "selected"), "disabled": _flag(item, "disabled")})
    table_id = identity
    attrs.update({"id": identity + "--lq-wrap", "class": f"lq-table-shell lq-surface lq-table-shell--{mode} lq-table-shell--{density}", "data-lq-table": ""})
    head, body = [], []
    selection_header = identity + "--lq-select"
    eligible = [row for row in normalized_rows if not row["disabled"]]
    selected = sum(row["selected"] for row in eligible)
    if selectable:
        state = "mixed" if 0 < selected < len(eligible) else "true" if eligible and selected == len(eligible) else "false"
        master_attrs = {"data-lq-select-all": "", "aria-checked": state}
        if state == "true":
            master_attrs["checked"] = ""
        if not eligible:
            master_attrs["disabled"] = ""
        head.append(_node("th", {"scope": "col", "id": selection_header, "role": "columnheader", "class": "lq-table__selection"}, [_checkbox("选择本页可操作行", master_attrs)]))
    for col in cols:
        th_attrs = {"scope": "col", "role": "columnheader", "id": identity + "--lq-col-" + col["key"], "data-align": col["align"]}
        label = [col["label"]]
        if col["sortable"]:
            th_attrs["aria-sort"] = col["sort"]
            label = [_node("button", {"type": "button", "class": "lq-table__sort", "data-lq-sort": col["key"], "aria-label": "按" + col["label"] + "排序"}, [col["label"], _node("span", {"aria-hidden": "true", "class": "lq-table__sort-mark"}, ["↕"])])]
        # Opt-in per column: a header that has to carry its own controls gets the
        # same slot treatment as a cell. Columns that do not ask for it keep the
        # exact markup they had, so no existing table changes shape.
        if col["slot"]:
            slot_name = "col:" + col["key"]
            label = [_node("div", {"class": "lq-table__colhead", "data-lq-slot": slot_name}, [*label, {"slot": slot_name}])]
        head.append(_node("th", th_attrs, label))
    selection_name = _text(p.get("selection_name", "selected"), True)
    for row in normalized_rows:
        row_id = identity + "--lq-row-" + row["key"]
        cells = []
        if selectable:
            check_attrs = {"data-lq-select-row": "", "name": selection_name, "value": row["key"]}
            if row["selected"]:
                check_attrs["checked"] = ""
            if row["disabled"]:
                check_attrs["disabled"] = ""
            cells.append(_node("td", {"role": "cell", "headers": selection_header + (" " + row_id if row_headers else ""), "data-label": "选择", "class": "lq-table__selection"}, [_checkbox("选择 " + row["label"], check_attrs)]))
        for col in cols:
            cell_attrs = {"role": "rowheader" if col["rowHeader"] else "cell", "headers": identity + "--lq-col-" + col["key"] + (" " + row_id if row_headers and not col["rowHeader"] else ""), "data-label": col["label"], "data-align": col["align"]}
            if col["rowHeader"]:
                cell_attrs.update({"scope": "row", "id": row_id})
            name = "cell:" + row["key"] + ":" + col["key"]
            cells.append(_node("th" if col["rowHeader"] else "td", cell_attrs, [_node("span", {"class": "lq-table__label", "aria-hidden": "true"}, [col["label"]]), _node("div", {"class": "lq-table__cell", "data-lq-slot": name}, [row["cells"][col["key"]], {"slot": name}])]))
        body.append(_node("tr", {"role": "row", "data-lq-row-key": row["key"]}, cells))
    table = _node("table", {"id": table_id, "class": "lq-table", "role": "table"}, [_node("caption", {"id": identity + "--lq-caption"}, [caption]), _node("thead", {"role": "rowgroup"}, [_node("tr", {"role": "row"}, head)]), _node("tbody", {"role": "rowgroup"}, body)])
    return _node("div", attrs, [_node("div", {"class": "lq-table__scroll", "tabindex": "0", "role": "region", "aria-labelledby": identity + "--lq-caption"}, [table]), _node("div", {"class": "lq-table__empty", "data-lq-slot": "empty"}, [{"slot": "empty"}])])


def lq_table_props(component, **p):
    if component not in TABLE_KINDS or any(key in p for key in ("html", "rawHTML", "raw_html")):
        raise ValueError("Invalid table component or raw HTML")
    attrs = _attrs(p.get("attrs"))
    if p.get("id") is not None:
        attrs["id"] = _key(p["id"], True)
    if component == "table":
        return _table(p, attrs)
    if component == "result_count":
        state = _choice(p.get("state", "ready"), ("ready", "loading"))
        label = _text(p.get("label", "条结果"), True)
        count = _number(p["count"]) if p.get("count") is not None else None
        if state == "ready" and count is None:
            raise ValueError("Ready results require a known count")
        attrs.update({"class": "lq-result-count", "data-state": "loading" if state == "loading" else "empty" if count == 0 else "ready"})
        if state == "loading":
            attrs["aria-busy"] = "true"
        return _node("p", attrs, ["正在加载…" if state == "loading" else str(count) + " " + label])
    if component == "bulk_bar":
        count = _number(p.get("selected_count"))
        actions = p.get("actions", [])
        if not isinstance(actions, (list, tuple)):
            raise ValueError("Bulk actions must be a list")
        buttons = [_button(item) for item in actions]
        attrs.update({"class": "lq-bulk-bar", "role": "group", "aria-label": _text(p.get("label", "批量操作"), True)})
        if count == 0:
            attrs["hidden"] = ""
        return _node("div", attrs, [_node("span", {"class": "lq-bulk-bar__count"}, ["已选择 " + str(count) + " 项"]), _node("div", {"class": "lq-bulk-bar__actions"}, buttons if count else [])])
    page, total = _number(p.get("page")), _number(p.get("total_pages"))
    if (total == 0 and page != 0) or (total > 0 and not 1 <= page <= total):
        raise ValueError("Page is outside the declared total")
    disabled = _flag(p, "disabled")
    links = p.get("links")
    if links is not None:
        if not isinstance(links, Mapping):
            raise ValueError("Pager links must map page numbers to safe URLs")
        for key, value in links.items():
            if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key) or int(key) > total:
                raise ValueError("Invalid pager link page")
            if _url(value) is None:
                raise ValueError("Pager links require explicit safe URLs")
    attrs.update({"class": "lq-pager", "aria-label": _text(p.get("label", "分页"), True)})
    def control(target, label, blocked=False, current=False):
        a = {"class": "lq-pager__control", "data-lq-page": str(target), "aria-label": label}
        if current:
            a["aria-current"] = "page"
        blocked = blocked or disabled or current
        if links is not None and not blocked:
            if str(target) not in links:
                raise ValueError("Every enabled displayed page needs an explicit href")
            a["href"] = _url(links[str(target)])
            return _node("a", a, [label])
        a["type"] = "button"
        if blocked:
            a.update({"disabled": "", "aria-disabled": "true"})
        return _node("button", a, [label])
    children = [control(max(1, page - 1), "上一页", page <= 1)]
    pages = sorted({1, total, *range(max(1, page - 1), min(total, page + 1) + 1)}) if total else []
    previous = 0
    for target in pages:
        if previous and target - previous > 1:
            children.append(_node("span", {"class": "lq-pager__ellipsis", "aria-hidden": "true"}, ["…"]))
        item = control(target, "第 " + str(target) + " 页", current=target == page)
        item["children"] = [str(target)]
        children.append(item)
        previous = target
    children += [control(min(total, page + 1), "下一页", page >= total), _node("span", {"class": "lq-pager__summary"}, ["第 " + str(page) + " / " + str(total) + " 页"])]
    return _node("nav", attrs, children)
